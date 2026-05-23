#!/usr/bin/env python3
"""Query UK Land Registry Open Data API for property transactions and price indices.

All metadata is cached locally after first fetch. Only data queries hit
the live API on subsequent runs. Cache lives in .cache/land-registry/.

Rate limit: 1 request/second (conservative for public API).

Data products:
    Price Paid Data     Transaction records with price, date, property type, postcode.
    UK House Price Index Monthly index by local authority.

Commands:
    search          Search available regions/districts.
    price-paid      Fetch transaction records for a district.
    hpi             Fetch UK House Price Index for an area.

Recommended workflow:
    1. search --term "exampleton"                       # find region names  (cached)
    2. price-paid --district "CENTRAL DISTRICT" --from 2020 --to 2024 --out prices.csv
    3. hpi --area "the metro region" --from 2010 --to 2024 --out hpi.csv

Usage:
    python3 tools/land-registry-api.py search --term TERM
    python3 tools/land-registry-api.py price-paid --district DISTRICT [--from YEAR] [--to YEAR] [--type TYPE] [--out FILE]
    python3 tools/land-registry-api.py hpi --area AREA [--from YEAR] [--to YEAR] [--out FILE]

All metadata commands accept --refresh to bypass cache.

Examples:
    python3 tools/land-registry-api.py search --term "exampleton"
    python3 tools/land-registry-api.py price-paid --district "CENTRAL DISTRICT" --from 2015 --to 2024 --out prices.csv
    python3 tools/land-registry-api.py price-paid --district "WESTGATE" --from 2020 --to 2024 --type "terraced"
    python3 tools/land-registry-api.py hpi --area "the metro region" --from 2010 --to 2024 --out hpi.csv
    python3 tools/land-registry-api.py hpi --area "England" --from 2015 --to 2024

Property types for price-paid: detached, semi-detached, terraced, flat-maisonette, other
Estate types: freehold, leasehold
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PPD_BASE = "https://landregistry.data.gov.uk/data/ppi"
HPI_BASE = "https://landregistry.data.gov.uk/data/ukhpi"

# Known HPI region slugs (slug → display name)
HPI_REGIONS = {
    "england-and-wales": "England and Wales",
    "england": "England",
    "wales": "Wales",
    "greater-london": "Greater London",
    "metro-region": "the metro region",
    "west-midlands-region": "West Midlands (Region)",
    "east-midlands": "East Midlands",
    "north-west": "North West",
    "north-east": "North East",
    "south-east": "South East",
    "south-west": "South West",
    "east-of-england": "East of England",
    "yorkshire-and-the-humber": "Yorkshire and the Humber",
    "central-district": "Central District",
    "central-district": "City of Westgate",
    "northgate": "Northgate",
    "eastgate": "Eastgate",
    "hillgate": "Hillgate",
    "rivergate": "Rivergate",
    "midgate": "Midgate",
    "valegate": "Valegate",
    "southgate": "Southgate",
    "lakegate": "Lakegate",
    "city-of-london": "City of London",
    "city-of-westminster": "City of Westminster",
    "birmingham": "Birmingham",
    "leeds": "Leeds",
    "liverpool": "Liverpool",
    "sheffield": "Sheffield",
    "bristol-city-of": "Bristol",
    "city-of-edinburgh": "City of Edinburgh",
    "glasgow-city": "Glasgow City",
    "cardiff": "Cardiff",
}

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # seconds

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "land-registry")

METADATA_TTL = 7 * 86400  # 7 days

# Property type mapping for Land Registry API
PROPERTY_TYPES = {
    "detached": "lrcommon:detached",
    "semi-detached": "lrcommon:semi-detached",
    "terraced": "lrcommon:terraced",
    "flat-maisonette": "lrcommon:flat-maisonette",
    "other": "lrcommon:otherPropertyType",
}

ESTATE_TYPES = {
    "freehold": "lrcommon:freehold",
    "leasehold": "lrcommon:leasehold",
}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(key):
    safe = hashlib.sha256(key.encode()).hexdigest()[:16]
    readable = key.replace("/", "_").replace("?", "_").replace("&", "_")[:60]
    return os.path.join(CACHE_DIR, f"{readable}_{safe}.json")


def _cache_read(key, ttl):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) > ttl:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_write(key, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _fetch(url, timeout=120):
    """Fetch from Land Registry API. Returns raw text."""
    _rate_limit()

    headers = {"User-Agent": "QLE-Infrastructure/1.0", "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        msgs = {
            400: f"Bad request. Check parameters. URL: {url}\n{body}",
            404: f"Resource not found. URL: {url}",
            429: "Rate limited. Wait before retrying.",
            500: f"Land Registry server error (500). URL: {url}",
            503: "Service temporarily unavailable. Try again later.",
        }
        print(
            f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(url, timeout=120):
    """Fetch and parse JSON from Land Registry API."""
    data = _fetch(url, timeout)
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        print(f"Error: Could not parse JSON response from {url}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key, url, ttl=METADATA_TTL, refresh=False, timeout=120):
    """Fetch with caching. Returns raw text."""
    if not refresh:
        cached = _cache_read(key, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached

    data = _fetch(url, timeout)
    _cache_write(key, data)
    return data


# ---------------------------------------------------------------------------
# Price Paid helpers
# ---------------------------------------------------------------------------


def _parse_ppd_record(item):
    """Parse a single Price Paid Data JSON-LD record into a flat dict."""
    addr = item.get("propertyAddress", {})
    return {
        "transaction_id": item.get("transactionId", ""),
        "price": item.get("pricePaid", ""),
        "date": item.get("transactionDate", ""),
        "postcode": addr.get("postcode", ""),
        "paon": addr.get("paon", ""),
        "saon": addr.get("saon", ""),
        "street": addr.get("street", ""),
        "locality": addr.get("locality", ""),
        "town": addr.get("town", ""),
        "district": addr.get("district", ""),
        "county": addr.get("county", ""),
        "property_type": _simplify_uri(item.get("propertyType", "")),
        "new_build": str(item.get("newBuild", "")).lower(),
        "estate_type": _simplify_uri(item.get("estateType", "")),
        "transaction_category": _simplify_uri(
            item.get("transactionCategory", "")
        ),
    }


def _simplify_uri(val):
    """Extract the local part from a URI or return the value as-is."""
    if isinstance(val, str) and "/" in val:
        return val.rsplit("/", 1)[-1]
    return str(val) if val else ""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Search available regions/districts from the built-in region list."""
    term = args.term or ""
    print(f"Searching Land Registry regions for '{term}'...", file=sys.stderr)

    regions = [(name, slug) for slug, name in sorted(HPI_REGIONS.items(), key=lambda x: x[1])]

    # Filter
    if term:
        t = term.lower()
        regions = [(n, s) for n, s in regions if t in n.lower() or t in s.lower()]

    if not regions:
        print("No regions found.", file=sys.stderr)
        return

    print(f"\n{'REGION':<45} SLUG (use with hpi --area)")
    print("-" * 80)
    for name, slug in regions:
        print(f"{name:<45} {slug}")

    print(f"\n{len(regions)} regions found.", file=sys.stderr)
    print("For Price Paid, use district names (UPPERCASE): EXAMPLETON, WESTGATE, etc.", file=sys.stderr)


def cmd_price_paid(args):
    """Fetch Price Paid Data transaction records."""
    district = args.district.upper()
    print(f"Fetching Price Paid Data for {district}...", file=sys.stderr)

    all_records = []
    page = 0
    page_size = 200

    while True:
        params = {
            "propertyAddress.district": district,
            "min-pricePaid": "0",
            "_pageSize": str(page_size),
            "_page": str(page),
        }

        if args.type and args.type in PROPERTY_TYPES:
            params["propertyType"] = PROPERTY_TYPES[args.type]

        if args.estate and args.estate in ESTATE_TYPES:
            params["estateType"] = ESTATE_TYPES[args.estate]

        query = urllib.parse.urlencode(params)
        url = f"{PPD_BASE}/transaction-record.json?{query}"

        raw = _fetch(url, timeout=180)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"Error: Could not parse response on page {page}.", file=sys.stderr)
            break

        items = data.get("result", {}).get("items", [])
        if not items:
            break

        for item in items:
            rec = _parse_ppd_record(item)

            # Year filter
            date_str = rec.get("date", "")
            if date_str and args.year_from:
                try:
                    yr = int(date_str[:4])
                    if yr < args.year_from:
                        continue
                    if args.year_to and yr > args.year_to:
                        continue
                except (ValueError, IndexError):
                    pass

            all_records.append(rec)

        print(
            f"  Page {page}: {len(items)} records (total: {len(all_records)})",
            file=sys.stderr,
        )

        # Check if there are more pages
        total = data.get("result", {}).get("totalCount")
        if total is not None:
            if (page + 1) * page_size >= total:
                break
        elif len(items) < page_size:
            break

        page += 1

        # Safety limit
        if page > 500:
            print("Warning: Hit 500-page limit. Data may be incomplete.", file=sys.stderr)
            break

    if not all_records:
        print("No transactions found.", file=sys.stderr)
        return

    # Output
    fieldnames = [
        "transaction_id", "price", "date", "postcode", "paon", "saon",
        "street", "locality", "town", "district", "county",
        "property_type", "new_build", "estate_type", "transaction_category",
    ]

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_records)
        print(f"\nWrote {len(all_records)} transactions to {args.out}", file=sys.stderr)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)

    # Summary stats
    prices = [int(r["price"]) for r in all_records if r.get("price")]
    if prices:
        print(f"\nSummary ({district}):", file=sys.stderr)
        print(f"  Transactions: {len(all_records)}", file=sys.stderr)
        print(f"  Min price:    £{min(prices):,}", file=sys.stderr)
        print(f"  Max price:    £{max(prices):,}", file=sys.stderr)
        print(f"  Mean price:   £{sum(prices) // len(prices):,}", file=sys.stderr)
        print(f"  Median price: £{sorted(prices)[len(prices) // 2]:,}", file=sys.stderr)

        # Date range
        dates = sorted(r["date"] for r in all_records if r.get("date"))
        if dates:
            print(f"  Date range:   {dates[0][:10]} to {dates[-1][:10]}", file=sys.stderr)

        # Type breakdown
        types = {}
        for r in all_records:
            t = r.get("property_type", "unknown")
            types[t] = types.get(t, 0) + 1
        print("  By type:", file=sys.stderr)
        for t, count in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {t}: {count}", file=sys.stderr)


def cmd_hpi(args):
    """Fetch UK House Price Index data using per-region/month endpoints."""
    area = args.area
    print(f"Fetching HPI for '{area}'...", file=sys.stderr)

    # Resolve area to slug
    slug = None
    area_lower = area.lower().replace(" ", "-")
    # Direct slug match
    if area_lower in HPI_REGIONS:
        slug = area_lower
    else:
        # Fuzzy match by display name
        for s, name in HPI_REGIONS.items():
            if area.lower() in name.lower() or area.lower() in s:
                slug = s
                break

    if not slug:
        print(f"Error: Could not find region '{area}'.", file=sys.stderr)
        print("Available regions:", file=sys.stderr)
        for s, name in sorted(HPI_REGIONS.items(), key=lambda x: x[1]):
            print(f"  {name:<40} (slug: {s})", file=sys.stderr)
        sys.exit(1)

    display_name = HPI_REGIONS.get(slug, slug)
    print(f"  Resolved to: {display_name} (slug: {slug})", file=sys.stderr)

    # Determine date range
    year_from = args.year_from or 2010
    year_to = args.year_to or 2025

    all_records = []

    for year in range(year_from, year_to + 1):
        for month in range(1, 13):
            date_str = f"{year}-{month:02d}"
            url = f"{HPI_BASE}/region/{slug}/month/{date_str}.json"
            cache_key = f"hpi_{slug}_{date_str}"

            raw = _fetch_cached(cache_key, url, ttl=METADATA_TTL, refresh=False)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            result = data.get("result", {})
            primary = result.get("primaryTopic", result)

            avg_price = primary.get("ukhpiAveragePrice", primary.get("averagePrice", ""))
            hpi_val = primary.get("ukhpiHousePriceIndex", primary.get("housePriceIndex", ""))
            pct_monthly = primary.get("ukhpiPercentageChange", primary.get("percentageChange", ""))
            pct_yearly = primary.get("ukhpiPercentageAnnualChange", primary.get("percentageAnnualChange", ""))
            vol = primary.get("ukhpiSalesVolume", primary.get("salesVolume", ""))
            avg_detached = primary.get("ukhpiAveragePriceDetached", primary.get("averagePriceDetached", ""))
            avg_semi = primary.get("ukhpiAveragePriceSemiDetached", primary.get("averagePriceSemiDetached", ""))
            avg_terraced = primary.get("ukhpiAveragePriceTerraced", primary.get("averagePriceTerraced", ""))
            avg_flat = primary.get("ukhpiAveragePriceFlatMaisonette", primary.get("averagePriceFlatMaisonette", ""))

            if not avg_price and not hpi_val:
                continue

            rec = {
                "region": display_name,
                "month": date_str,
                "average_price": avg_price,
                "house_price_index": hpi_val,
                "percentage_change_monthly": pct_monthly,
                "percentage_change_yearly": pct_yearly,
                "sales_volume": vol,
                "average_price_detached": avg_detached,
                "average_price_semi": avg_semi,
                "average_price_terraced": avg_terraced,
                "average_price_flat": avg_flat,
            }
            all_records.append(rec)

        print(f"  {year}: {len(all_records)} total observations", file=sys.stderr)

    if not all_records:
        print(f"No HPI data found for '{area}' ({slug}).", file=sys.stderr)
        print("Use 'search --term NAME' to find available region slugs.", file=sys.stderr)
        return

    # Sort by month
    all_records.sort(key=lambda r: r.get("month", ""))

    # Output
    fieldnames = [
        "region", "month", "average_price", "house_price_index",
        "percentage_change_monthly", "percentage_change_yearly",
        "sales_volume", "average_price_detached", "average_price_semi",
        "average_price_terraced", "average_price_flat",
    ]

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_records)
        print(f"\nWrote {len(all_records)} observations to {args.out}", file=sys.stderr)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)

    # Summary stats
    prices = [
        float(r["average_price"])
        for r in all_records
        if r.get("average_price") and str(r["average_price"]).strip()
    ]
    if prices:
        months = sorted(r["month"] for r in all_records if r.get("month"))
        print(f"\nSummary (HPI — {display_name}):", file=sys.stderr)
        print(f"  Observations:   {len(all_records)}", file=sys.stderr)
        if months:
            print(f"  Period:         {months[0]} to {months[-1]}", file=sys.stderr)
        print(f"  Avg price low:  £{min(prices):,.0f}", file=sys.stderr)
        print(f"  Avg price high: £{max(prices):,.0f}", file=sys.stderr)
        print(f"  Latest avg:     £{prices[-1]:,.0f}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query UK Land Registry Open Data API (with local cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search --term AREA             (cached — find region names)\n"
            "  2. price-paid --district DISTRICT  (paginated transaction download)\n"
            "  3. hpi --area AREA                 (house price index time series)\n"
            "\n"
            "the metro region districts:\n"
            "  CENTRAL DISTRICT, WESTGATE, SOUTHGATE, MIDGATE,\n"
            "  VALEGATE, HILLGATE, RIVERGATE, EASTGATE, NORTHGATE, LAKEGATE\n"
            "\n"
            "Property types: detached, semi-detached, terraced, flat-maisonette, other\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search available regions/districts")
    p.add_argument("--term", "-t", help="Search keyword")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # price-paid
    p = sub.add_parser("price-paid", help="Fetch Price Paid transaction records")
    p.add_argument(
        "--district", "-d", required=True,
        help="District name (e.g. 'CENTRAL DISTRICT')",
    )
    p.add_argument("--from", dest="year_from", type=int, help="Start year (inclusive)")
    p.add_argument("--to", dest="year_to", type=int, help="End year (inclusive)")
    p.add_argument(
        "--type", choices=list(PROPERTY_TYPES.keys()),
        help="Filter by property type",
    )
    p.add_argument(
        "--estate", choices=list(ESTATE_TYPES.keys()),
        help="Filter by estate type",
    )
    p.add_argument("--out", "-o", help="Output CSV file path")

    # hpi
    p = sub.add_parser("hpi", help="Fetch UK House Price Index")
    p.add_argument("--area", "-a", required=True, help="Area name (e.g. 'the metro region')")
    p.add_argument("--from", dest="year_from", type=int, help="Start year (inclusive)")
    p.add_argument("--to", dest="year_to", type=int, help="End year (inclusive)")
    p.add_argument("--out", "-o", help="Output CSV file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "price-paid": cmd_price_paid,
        "hpi": cmd_hpi,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

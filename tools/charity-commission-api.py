#!/usr/bin/env python3
"""Query Charity Commission for England and Wales data.

Uses the Charity Commission API for search and detail lookups. All metadata
is cached locally after first fetch. Cache lives in .cache/charity-commission/.

No API key required for basic queries.

Rate limit: 1 request/second (conservative for public API).

Commands:
    search      Search charities by name/keyword, with optional income filter.
    detail      Get full charity details (income, expenditure, trustees, objects).
    accounts    Financial history for a charity.

Recommended workflow:
    1. search --term "exampleton" --min-income 1000000    # find charities
    2. detail 1234567                                     # full details   (cached 7 days)
    3. accounts 1234567                                   # financials     (cached 7 days)

Usage:
    python3 tools/charity-commission-api.py search --term "exampleton" [--min-income N] [--out FILE]
    python3 tools/charity-commission-api.py detail CHARITY_NUMBER
    python3 tools/charity-commission-api.py accounts CHARITY_NUMBER [--out FILE]

All cached commands accept --refresh to bypass cache.

Examples:
    python3 tools/charity-commission-api.py search --term "exampleton" --min-income 1000000 --out charities.csv
    python3 tools/charity-commission-api.py search --term "housing" --min-income 5000000
    python3 tools/charity-commission-api.py detail 1164897
    python3 tools/charity-commission-api.py accounts 1164897 --out accounts.csv

Note: The Charity Commission API returns data for charities registered in
England and Wales. Scottish charities are under OSCR; Northern Irish under
the Charity Commission for Northern Ireland.
"""

import argparse
import csv
import hashlib
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

# Charity Commission API (v1 — public, no key needed)
BASE_URL = "https://api.charitycommission.gov.uk/register/api"

# Alternative: CharityBase GraphQL (more structured, but may require key)
# We use the official CCEW API as primary

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # seconds

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "charity-commission")

METADATA_TTL = 7 * 86400  # 7 days


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


def _fetch(url, timeout=60):
    """Fetch from Charity Commission API. Returns raw text."""
    _rate_limit()

    headers = {
        "User-Agent": "QLE-Infrastructure/1.0",
        "Accept": "application/json",
    }
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
            404: f"Charity not found. URL: {url}",
            429: "Rate limited. Wait before retrying.",
            500: f"Charity Commission server error (500). URL: {url}",
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


def _fetch_json(url, timeout=60):
    """Fetch and parse JSON."""
    data = _fetch(url, timeout)
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        print(f"Error: Could not parse JSON from {url}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key, url, ttl=METADATA_TTL, refresh=False, timeout=60):
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
# API helpers
# ---------------------------------------------------------------------------


def _search_charities(search_term, page=0, page_size=20):
    """Search charities using the CCEW API.

    The CCEW register API endpoint:
    https://api.charitycommission.gov.uk/register/api/allcharitydetailsV2?searchText=TERM&pageNumber=N&pageSize=S
    """
    params = {
        "searchText": search_term,
        "pageNumber": str(page),
        "pageSize": str(page_size),
    }
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/allcharitydetailsV2?{query}"
    return _fetch_json(url)


def _get_charity_detail(charity_number):
    """Get charity detail from CCEW API."""
    url = f"{BASE_URL}/charitydetails?regcharitynumber={charity_number}&subsid=0"
    return url


def _get_charity_accounts(charity_number):
    """Get charity financial accounts URL."""
    url = f"{BASE_URL}/financialhistory?regcharitynumber={charity_number}&subsid=0"
    return url


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _write_csv(records, fieldnames, out_path=None):
    """Write records as CSV to file or stdout."""
    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        print(f"\nWrote {len(records)} records to {out_path}", file=sys.stderr)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Search charities by name/keyword."""
    term = args.term
    print(f"Searching charities for '{term}'...", file=sys.stderr)

    all_records = []
    page = 0
    page_size = 50
    max_pages = 10  # Safety limit

    while page < max_pages:
        params = {
            "searchText": term,
            "pageNumber": str(page),
            "pageSize": str(page_size),
        }
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}/allcharitydetailsV2?{query}"

        try:
            data = _fetch_json(url)
        except SystemExit:
            # If the V2 endpoint fails, try the simpler search
            print("Trying alternative search endpoint...", file=sys.stderr)
            url_alt = f"https://api.charitycommission.gov.uk/register/api/searchcharities?searchText={urllib.parse.quote(term)}&pageNumber={page}&pageSize={page_size}"
            try:
                data = _fetch_json(url_alt)
            except SystemExit:
                if all_records:
                    break
                print("Search API unavailable. Try the web interface at https://register-of-charities.charitycommission.gov.uk/", file=sys.stderr)
                return

        # Handle both list and dict responses
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items", data.get("charities", data.get("CharityDetails", [])))
            if not items and "charity_name" in data:
                items = [data]

        if not items:
            break

        for item in items:
            # Normalize field names — API may use various casing
            rec = {
                "charity_number": str(
                    item.get("registered_charity_number",
                    item.get("RegisteredCharityNumber",
                    item.get("charity_registration_number",
                    item.get("reg_charity_number", ""))))
                ),
                "name": (
                    item.get("charity_name",
                    item.get("CharityName",
                    item.get("name", "")))
                ),
                "status": (
                    item.get("charity_registration_status",
                    item.get("RegistrationStatus",
                    item.get("status", "")))
                ),
                "postcode": (
                    item.get("charity_contact_postcode",
                    item.get("Postcode",
                    item.get("postcode", "")))
                ),
                "latest_income": str(
                    item.get("latest_income",
                    item.get("LatestIncome",
                    item.get("income", "")))
                ),
                "latest_expenditure": str(
                    item.get("latest_expenditure",
                    item.get("LatestExpenditure",
                    item.get("expenditure", "")))
                ),
                "date_registered": (
                    item.get("date_of_registration",
                    item.get("RegistrationDate",
                    item.get("registration_date", "")))
                ),
                "date_removed": (
                    item.get("date_of_removal",
                    item.get("RemovalDate",
                    item.get("removal_date", "")))
                ),
                "charity_activities": (
                    item.get("charity_activities",
                    item.get("Activities",
                    item.get("activities", "")))
                )[:200] if (
                    item.get("charity_activities",
                    item.get("Activities",
                    item.get("activities", "")))
                ) else "",
            }

            # Income filter
            if args.min_income:
                try:
                    inc = float(rec["latest_income"]) if rec["latest_income"] else 0
                    if inc < args.min_income:
                        continue
                except (ValueError, TypeError):
                    continue

            all_records.append(rec)

        print(f"  Page {page}: {len(items)} items (matched: {len(all_records)})", file=sys.stderr)

        if len(items) < page_size:
            break
        page += 1

    if not all_records:
        print("No charities found matching criteria.", file=sys.stderr)
        return

    # Sort by income descending
    def _sort_income(r):
        try:
            return float(r.get("latest_income", 0) or 0)
        except (ValueError, TypeError):
            return 0
    all_records.sort(key=_sort_income, reverse=True)

    fieldnames = [
        "charity_number", "name", "status", "postcode",
        "latest_income", "latest_expenditure",
        "date_registered", "date_removed", "charity_activities",
    ]

    if args.out:
        _write_csv(all_records, fieldnames, args.out)
    else:
        print(f"\n{'NUMBER':<12} {'INCOME':<15} {'STATUS':<12} NAME")
        print("-" * 90)
        for r in all_records:
            inc = r["latest_income"]
            try:
                inc_fmt = f"£{float(inc):,.0f}" if inc else "N/A"
            except (ValueError, TypeError):
                inc_fmt = inc or "N/A"
            print(
                f"{r['charity_number']:<12} "
                f"{inc_fmt:<15} "
                f"{r['status']:<12} "
                f"{r['name'][:50]}"
            )

    # Summary
    incomes = []
    for r in all_records:
        try:
            v = float(r.get("latest_income", 0) or 0)
            if v > 0:
                incomes.append(v)
        except (ValueError, TypeError):
            pass

    print(f"\n{len(all_records)} charities found.", file=sys.stderr)
    if incomes:
        print(f"  Total income: £{sum(incomes):,.0f}", file=sys.stderr)
        print(f"  Mean income:  £{sum(incomes) / len(incomes):,.0f}", file=sys.stderr)
        print(f"  Max income:   £{max(incomes):,.0f}", file=sys.stderr)


def cmd_detail(args):
    """Get full charity details."""
    charity_num = args.charity_number
    print(f"Charity detail for {charity_num}...", file=sys.stderr)

    cache_key = f"detail_{charity_num}"
    refresh = getattr(args, "refresh", False)

    url = _get_charity_detail(charity_num)
    raw = _fetch_cached(cache_key, url, refresh=refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse charity detail.", file=sys.stderr)
        sys.exit(1)

    # Handle both single object and list responses
    if isinstance(data, list):
        if not data:
            print("No data found.", file=sys.stderr)
            return
        data = data[0]

    # Display — normalize keys for various API response formats
    def _get(d, *keys, default="N/A"):
        for k in keys:
            if k in d:
                val = d[k]
                if val is not None and val != "":
                    return val
        return default

    print(f"\n{'=' * 70}")
    print(f"  Charity:    {_get(data, 'charity_name', 'CharityName', 'name')}")
    print(f"  Number:     {_get(data, 'registered_charity_number', 'RegisteredCharityNumber', 'charity_registration_number')}")
    print(f"  Status:     {_get(data, 'charity_registration_status', 'RegistrationStatus', 'status')}")
    print(f"  Registered: {_get(data, 'date_of_registration', 'RegistrationDate', 'registration_date')}")

    removed = _get(data, "date_of_removal", "RemovalDate", "removal_date", default="")
    if removed:
        print(f"  Removed:    {removed}")

    print(f"  Postcode:   {_get(data, 'charity_contact_postcode', 'Postcode', 'postcode')}")
    print(f"  Phone:      {_get(data, 'charity_contact_phone', 'Phone', 'phone')}")
    print(f"  Email:      {_get(data, 'charity_contact_email', 'Email', 'email')}")
    print(f"  Website:    {_get(data, 'charity_contact_web', 'Website', 'web')}")

    income = _get(data, "latest_income", "LatestIncome", "income", default="")
    expenditure = _get(data, "latest_expenditure", "LatestExpenditure", "expenditure", default="")
    if income:
        try:
            print(f"  Income:     £{float(income):,.0f}")
        except (ValueError, TypeError):
            print(f"  Income:     {income}")
    if expenditure:
        try:
            print(f"  Expenditure: £{float(expenditure):,.0f}")
        except (ValueError, TypeError):
            print(f"  Expenditure: {expenditure}")

    activities = _get(data, "charity_activities", "Activities", "activities", default="")
    if activities and activities != "N/A":
        print(f"\n  Activities:")
        # Word-wrap at 70 chars
        words = str(activities).split()
        line = "    "
        for w in words:
            if len(line) + len(w) + 1 > 74:
                print(line)
                line = "    "
            line += w + " "
        if line.strip():
            print(line)

    objects = _get(data, "charity_charitable_objects", "CharitableObjects", "objects", default="")
    if objects and objects != "N/A":
        print(f"\n  Objects:")
        words = str(objects).split()
        line = "    "
        for w in words:
            if len(line) + len(w) + 1 > 74:
                print(line)
                line = "    "
            line += w + " "
        if line.strip():
            print(line)

    # Trustees/people
    trustees = data.get("trustees", data.get("Trustees", []))
    if isinstance(trustees, list) and trustees:
        print(f"\n  Trustees ({len(trustees)}):")
        for t in trustees[:20]:
            tname = t.get("name", t.get("Name", t.get("trustee_name", "")))
            print(f"    - {tname}")
        if len(trustees) > 20:
            print(f"    ... +{len(trustees) - 20} more")

    # Area of operation
    areas = data.get("charity_geographical_areas_of_operation",
                     data.get("AreaOfOperation", []))
    if isinstance(areas, list) and areas:
        print(f"\n  Areas of operation:")
        for a in areas[:10]:
            if isinstance(a, dict):
                print(f"    - {a.get('geographic_area_description', a.get('name', str(a)))}")
            else:
                print(f"    - {a}")

    print(f"{'=' * 70}")


def cmd_accounts(args):
    """Charity financial history."""
    charity_num = args.charity_number
    print(f"Financial history for charity {charity_num}...", file=sys.stderr)

    cache_key = f"accounts_{charity_num}"
    refresh = getattr(args, "refresh", False)

    url = _get_charity_accounts(charity_num)
    raw = _fetch_cached(cache_key, url, refresh=refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse financial history.", file=sys.stderr)
        sys.exit(1)

    # Handle various response formats
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items", data.get("financial_history",
                 data.get("FinancialHistory", [])))

    if not items:
        print("No financial history found.", file=sys.stderr)
        return

    records = []
    for item in items:
        rec = {
            "financial_year_end": (
                item.get("fin_period_end_date",
                item.get("FinancialYearEnd",
                item.get("financial_year_end", "")))
            ),
            "income": str(
                item.get("income",
                item.get("Income",
                item.get("total_gross_income", "")))
            ),
            "expenditure": str(
                item.get("expenditure",
                item.get("Expenditure",
                item.get("total_gross_expenditure", "")))
            ),
            "charitable_spending": str(
                item.get("charitable_spending",
                item.get("CharitableSpending",
                item.get("charity_raises_funds_from_grants", "")))
            ),
            "fundraising": str(
                item.get("income_generation_and_governance",
                item.get("Fundraising",
                item.get("income_from_fundraising", "")))
            ),
            "reserves": str(
                item.get("reserves",
                item.get("Reserves", ""))
            ),
            "employees": str(
                item.get("count_employees",
                item.get("Employees",
                item.get("employees", "")))
            ),
            "volunteers": str(
                item.get("count_volunteers",
                item.get("Volunteers",
                item.get("volunteers", "")))
            ),
        }
        records.append(rec)

    # Sort by year
    records.sort(key=lambda r: r.get("financial_year_end", ""))

    fieldnames = [
        "financial_year_end", "income", "expenditure",
        "charitable_spending", "fundraising",
        "reserves", "employees", "volunteers",
    ]

    if args.out:
        _write_csv(records, fieldnames, args.out)
    else:
        print(f"\n{'YEAR END':<14} {'INCOME':<16} {'EXPENDITURE':<16} {'EMPLOYEES':<10} RESERVES")
        print("-" * 80)
        for r in records:
            def _fmt_money(val):
                try:
                    return f"£{float(val):,.0f}" if val else "N/A"
                except (ValueError, TypeError):
                    return val or "N/A"

            print(
                f"{r['financial_year_end']:<14} "
                f"{_fmt_money(r['income']):<16} "
                f"{_fmt_money(r['expenditure']):<16} "
                f"{r['employees'] or 'N/A':<10} "
                f"{_fmt_money(r['reserves'])}"
            )

    # Summary
    incomes = []
    for r in records:
        try:
            v = float(r.get("income", 0) or 0)
            if v > 0:
                incomes.append(v)
        except (ValueError, TypeError):
            pass

    print(f"\n{len(records)} financial periods.", file=sys.stderr)
    if incomes:
        print(f"  Income range: £{min(incomes):,.0f} — £{max(incomes):,.0f}", file=sys.stderr)
        if len(incomes) >= 2:
            change = ((incomes[-1] - incomes[0]) / incomes[0]) * 100
            print(f"  Income change: {change:+.1f}% over period", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query Charity Commission for England and Wales (with local cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search --term KEYWORD [--min-income N]   (find charities)\n"
            "  2. detail CHARITY_NUMBER                    (cached 7 days)\n"
            "  3. accounts CHARITY_NUMBER                  (cached 7 days)\n"
            "\n"
            "No API key required. Data covers England and Wales.\n"
            "For Scottish charities, see OSCR: oscr.org.uk\n"
            "\n"
            "Web interface: https://register-of-charities.charitycommission.gov.uk/\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search charities by name/keyword")
    p.add_argument("--term", "-t", required=True, help="Search keyword")
    p.add_argument(
        "--min-income", type=float, default=None,
        help="Minimum latest income (e.g. 1000000)",
    )
    p.add_argument("--out", "-o", help="Output CSV file path")

    # detail
    p = sub.add_parser("detail", help="Full charity details")
    p.add_argument("charity_number", help="Registered charity number")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # accounts
    p = sub.add_parser("accounts", help="Financial history")
    p.add_argument("charity_number", help="Registered charity number")
    p.add_argument("--out", "-o", help="Output CSV file path")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "detail": cmd_detail,
        "accounts": cmd_accounts,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

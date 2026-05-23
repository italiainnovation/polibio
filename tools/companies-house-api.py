#!/usr/bin/env python3
"""Query Companies House REST API for UK company data.

All metadata is cached locally after first fetch. Cache lives in
.cache/companies-house/.

Requires COMPANIES_HOUSE_API_KEY env var. Get a free key at:
https://developer.company-information.service.gov.uk/

Rate limit: 600 requests / 5 minutes. This tool enforces 0.5s between
requests as a baseline.

Commands:
    search      Search companies by name.
    profile     Get company details (SIC codes, registered office, status).
    officers    List directors and secretaries.
    psc         Persons of significant control.
    filing      Recent filing history.

Recommended workflow:
    1. search --term "Renaker"              # find company number
    2. profile 12345678                     # company details       (cached 24h)
    3. officers 12345678                    # directors/secretaries (cached 24h)
    4. psc 12345678                         # significant control   (cached 24h)
    5. filing 12345678                      # recent filings        (live)

Usage:
    python3 tools/companies-house-api.py search --term "Renaker" [--out FILE]
    python3 tools/companies-house-api.py profile COMPANY_NUMBER
    python3 tools/companies-house-api.py officers COMPANY_NUMBER [--out FILE]
    python3 tools/companies-house-api.py psc COMPANY_NUMBER [--out FILE]
    python3 tools/companies-house-api.py filing COMPANY_NUMBER [--out FILE]

All cached commands accept --refresh to bypass cache.

Examples:
    python3 tools/companies-house-api.py search --term "Renaker" --out results.csv
    python3 tools/companies-house-api.py profile 08871095
    python3 tools/companies-house-api.py officers 08871095
    python3 tools/companies-house-api.py psc 08871095
    python3 tools/companies-house-api.py filing 08871095 --out filings.csv
"""

import argparse
import base64
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

BASE_URL = "https://api.company-information.service.gov.uk"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.5  # seconds — 600 req/5min = 2/sec, we use half

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "companies-house")

METADATA_TTL = 24 * 3600  # 24 hours for company data


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


def _get_api_key():
    key = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
    if not key:
        print(
            "Error: Set COMPANIES_HOUSE_API_KEY env var.\n"
            "Get a free key at https://developer.company-information.service.gov.uk/",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _fetch(path, params=None, timeout=60):
    """Fetch from Companies House API. Returns raw text."""
    api_key = _get_api_key()

    if params is None:
        params = {}

    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{path.lstrip('/')}"
    if query:
        url += ("&" if "?" in url else "?") + query

    _rate_limit()

    # Companies House uses Basic auth: API key as username, empty password
    credentials = base64.b64encode(f"{api_key}:".encode()).decode()
    headers = {
        "User-Agent": "QLE-Infrastructure/1.0",
        "Authorization": f"Basic {credentials}",
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
            401: "Unauthorized — check COMPANIES_HOUSE_API_KEY.",
            403: "Forbidden — API key may lack required permissions.",
            404: f"Company not found. URL: {url}",
            429: "Rate limited (600 req/5min). Wait before retrying.",
            500: f"Companies House server error (500). URL: {url}",
        }
        print(
            f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(path, params=None, timeout=60):
    """Fetch and parse JSON from Companies House API."""
    data = _fetch(path, params, timeout)
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        print(f"Error: Could not parse JSON from {path}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key, path, params=None, ttl=METADATA_TTL, refresh=False, timeout=60):
    """Fetch with caching. Returns raw text."""
    if not refresh:
        cached = _cache_read(key, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached

    data = _fetch(path, params, timeout)
    _cache_write(key, data)
    return data


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


def _format_address(addr):
    """Format an address dict into a single line."""
    if not addr:
        return ""
    parts = []
    for key in [
        "premises", "address_line_1", "address_line_2",
        "locality", "region", "postal_code", "country",
    ]:
        val = addr.get(key, "")
        if val:
            parts.append(str(val))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Search companies by name."""
    term = args.term
    print(f"Searching Companies House for '{term}'...", file=sys.stderr)

    params = {"q": term}
    if args.items_per_page:
        params["items_per_page"] = str(args.items_per_page)

    data = _fetch_json("search/companies", params=params)
    items = data.get("items", [])

    if not items:
        print("No companies found.", file=sys.stderr)
        return

    records = []
    for item in items:
        addr = item.get("address", {})
        records.append({
            "company_number": item.get("company_number", ""),
            "title": item.get("title", ""),
            "company_status": item.get("company_status", ""),
            "company_type": item.get("company_type", ""),
            "date_of_creation": item.get("date_of_creation", ""),
            "address": _format_address(addr),
            "sic_codes": ", ".join(item.get("sic_codes", []) or []),
        })

    fieldnames = [
        "company_number", "title", "company_status", "company_type",
        "date_of_creation", "address", "sic_codes",
    ]

    if args.out:
        _write_csv(records, fieldnames, args.out)
    else:
        print(f"\n{'NUMBER':<12} {'STATUS':<12} {'CREATED':<12} TITLE")
        print("-" * 90)
        for r in records:
            print(
                f"{r['company_number']:<12} "
                f"{r['company_status']:<12} "
                f"{r['date_of_creation']:<12} "
                f"{r['title']}"
            )

    print(f"\n{len(records)} companies found.", file=sys.stderr)


def cmd_profile(args):
    """Get company profile details."""
    co_num = args.company_number
    print(f"Company profile for {co_num}...", file=sys.stderr)

    cache_key = f"profile_{co_num}"
    refresh = getattr(args, "refresh", False)
    raw = _fetch_cached(cache_key, f"company/{co_num}", refresh=refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse company profile.", file=sys.stderr)
        sys.exit(1)

    # Display
    print(f"\n{'=' * 70}")
    print(f"  Company: {data.get('company_name', 'N/A')}")
    print(f"  Number:  {data.get('company_number', 'N/A')}")
    print(f"  Status:  {data.get('company_status', 'N/A')}")
    print(f"  Type:    {data.get('type', 'N/A')}")
    print(f"  Created: {data.get('date_of_creation', 'N/A')}")

    if data.get("date_of_cessation"):
        print(f"  Ceased:  {data['date_of_cessation']}")

    addr = data.get("registered_office_address", {})
    if addr:
        print(f"  Address: {_format_address(addr)}")

    sic = data.get("sic_codes", [])
    if sic:
        print(f"  SIC:     {', '.join(sic)}")

    accounts = data.get("accounts", {})
    if accounts:
        last_acc = accounts.get("last_accounts", {})
        next_due = accounts.get("next_due")
        if last_acc:
            print(f"  Last accounts:  {last_acc.get('made_up_to', 'N/A')} ({last_acc.get('type', '')})")
        if next_due:
            print(f"  Next accounts:  {next_due}")

    conf = data.get("confirmation_statement", {})
    if conf:
        print(f"  Last confirmation: {conf.get('last_made_up_to', 'N/A')}")
        if conf.get("next_due"):
            print(f"  Next due:          {conf['next_due']}")

    can_file = data.get("can_file", False)
    print(f"  Can file: {'Yes' if can_file else 'No'}")

    prev_names = data.get("previous_company_names", [])
    if prev_names:
        print("  Previous names:")
        for pn in prev_names:
            print(f"    {pn.get('effective_from', '')} — {pn.get('ceased_on', 'present')}: {pn.get('name', '')}")

    print(f"{'=' * 70}")


def cmd_officers(args):
    """List company officers (directors, secretaries)."""
    co_num = args.company_number
    print(f"Officers for {co_num}...", file=sys.stderr)

    cache_key = f"officers_{co_num}"
    refresh = getattr(args, "refresh", False)
    raw = _fetch_cached(cache_key, f"company/{co_num}/officers", refresh=refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse officers response.", file=sys.stderr)
        sys.exit(1)

    items = data.get("items", [])
    if not items:
        print("No officers found.", file=sys.stderr)
        return

    records = []
    for item in items:
        records.append({
            "name": item.get("name", ""),
            "officer_role": item.get("officer_role", ""),
            "appointed_on": item.get("appointed_on", ""),
            "resigned_on": item.get("resigned_on", ""),
            "nationality": item.get("nationality", ""),
            "country_of_residence": item.get("country_of_residence", ""),
            "occupation": item.get("occupation", ""),
            "address": _format_address(item.get("address", {})),
        })

    fieldnames = [
        "name", "officer_role", "appointed_on", "resigned_on",
        "nationality", "country_of_residence", "occupation", "address",
    ]

    if args.out:
        _write_csv(records, fieldnames, args.out)
    else:
        print(f"\n{'NAME':<35} {'ROLE':<15} {'APPOINTED':<12} {'RESIGNED':<12} OCCUPATION")
        print("-" * 100)
        for r in records:
            print(
                f"{r['name'][:34]:<35} "
                f"{r['officer_role']:<15} "
                f"{r['appointed_on']:<12} "
                f"{r['resigned_on']:<12} "
                f"{r['occupation']}"
            )

    active = sum(1 for r in records if not r["resigned_on"])
    print(f"\n{len(records)} officers ({active} active).", file=sys.stderr)


def cmd_psc(args):
    """Persons of significant control."""
    co_num = args.company_number
    print(f"PSC for {co_num}...", file=sys.stderr)

    cache_key = f"psc_{co_num}"
    refresh = getattr(args, "refresh", False)
    raw = _fetch_cached(
        cache_key, f"company/{co_num}/persons-with-significant-control",
        refresh=refresh,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse PSC response.", file=sys.stderr)
        sys.exit(1)

    items = data.get("items", [])
    if not items:
        print("No PSC records found.", file=sys.stderr)
        return

    records = []
    for item in items:
        natures = item.get("natures_of_control", [])
        records.append({
            "name": item.get("name", item.get("name_elements", {}).get("surname", "")),
            "kind": item.get("kind", ""),
            "notified_on": item.get("notified_on", ""),
            "ceased_on": item.get("ceased_on", ""),
            "nationality": item.get("nationality", ""),
            "country_of_residence": item.get("country_of_residence", ""),
            "natures_of_control": "; ".join(natures),
            "address": _format_address(item.get("address", {})),
        })

    fieldnames = [
        "name", "kind", "notified_on", "ceased_on",
        "nationality", "country_of_residence", "natures_of_control", "address",
    ]

    if args.out:
        _write_csv(records, fieldnames, args.out)
    else:
        print(f"\n{'NAME':<35} {'KIND':<30} {'NOTIFIED':<12} CONTROL")
        print("-" * 100)
        for r in records:
            control_short = r["natures_of_control"][:40]
            print(
                f"{r['name'][:34]:<35} "
                f"{r['kind'][:29]:<30} "
                f"{r['notified_on']:<12} "
                f"{control_short}"
            )

    active = sum(1 for r in records if not r["ceased_on"])
    print(f"\n{len(records)} PSC entries ({active} active).", file=sys.stderr)


def cmd_filing(args):
    """Recent filing history."""
    co_num = args.company_number
    print(f"Filing history for {co_num}...", file=sys.stderr)

    # Filing history is not cached — always live
    params = {"items_per_page": "50"}
    data = _fetch_json(f"company/{co_num}/filing-history", params=params)

    items = data.get("items", [])
    if not items:
        print("No filing history found.", file=sys.stderr)
        return

    records = []
    for item in items:
        records.append({
            "date": item.get("date", ""),
            "type": item.get("type", ""),
            "category": item.get("category", ""),
            "description": item.get("description", ""),
            "barcode": item.get("barcode", ""),
            "pages": item.get("pages", ""),
        })

    fieldnames = ["date", "type", "category", "description", "barcode", "pages"]

    if args.out:
        _write_csv(records, fieldnames, args.out)
    else:
        print(f"\n{'DATE':<12} {'TYPE':<15} {'CATEGORY':<20} DESCRIPTION")
        print("-" * 90)
        for r in records:
            print(
                f"{r['date']:<12} "
                f"{r['type']:<15} "
                f"{r['category']:<20} "
                f"{r['description'][:40]}"
            )

    print(f"\n{len(records)} filings.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query Companies House REST API (with local cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search --term NAME       (find company number)\n"
            "  2. profile COMPANY_NUMBER   (cached 24h)\n"
            "  3. officers COMPANY_NUMBER  (cached 24h)\n"
            "  4. psc COMPANY_NUMBER       (cached 24h)\n"
            "  5. filing COMPANY_NUMBER    (always live)\n"
            "\n"
            "Requires COMPANIES_HOUSE_API_KEY env var.\n"
            "Get a free key: https://developer.company-information.service.gov.uk/\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search companies by name")
    p.add_argument("--term", "-t", required=True, help="Company name search term")
    p.add_argument("--items-per-page", type=int, default=20, help="Results per page (default 20)")
    p.add_argument("--out", "-o", help="Output CSV file path")

    # profile
    p = sub.add_parser("profile", help="Get company profile details")
    p.add_argument("company_number", help="Company number (e.g. 08871095)")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # officers
    p = sub.add_parser("officers", help="List directors and secretaries")
    p.add_argument("company_number", help="Company number")
    p.add_argument("--out", "-o", help="Output CSV file path")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # psc
    p = sub.add_parser("psc", help="Persons of significant control")
    p.add_argument("company_number", help="Company number")
    p.add_argument("--out", "-o", help="Output CSV file path")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # filing
    p = sub.add_parser("filing", help="Recent filing history")
    p.add_argument("company_number", help="Company number")
    p.add_argument("--out", "-o", help="Output CSV file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "profile": cmd_profile,
        "officers": cmd_officers,
        "psc": cmd_psc,
        "filing": cmd_filing,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

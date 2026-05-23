#!/usr/bin/env python3
"""Query DART (Data Analysis, Retrieval and Transfer System) — Korea's
corporate disclosure system, the equivalent of SEC EDGAR.

API base: https://opendart.fss.or.kr/api/
Authentication: API key required (env var DART_API_KEY).
Register free at https://opendart.fss.or.kr/
Rate limit: ~1000 requests/day (free tier).
Cache lives in .cache/dart/.

DART provides:
  - Corporate filings (annual/quarterly reports, 10-K equivalents)
  - Cross-shareholding disclosures (critical for corporate group analysis)
  - Major shareholder reports (5% ownership threshold)
  - Executive compensation and officer changes
  - Corporate governance reports
  - Company fundamental information

Find a company's corp_code with the `search` command, or keep the companies
your study tracks in the CORP_CODES shorthand near the top of this file.

Commands:
    search      Search for companies by name (cached 7 days).
    company     Get company profile by corp_code (cached 7 days).
    filings     List filings for a company (always live).
    ownership   Get major shareholder / cross-ownership data (always live).
    preview     Fetch a small sample of filing data.

Recommended workflow:
    1. search "ExampleCorp" or search "ExampleCorp"    # find corp_code  (cached)
    2. company 00000000                         # company profile (cached)
    3. filings 00000000 --type A               # annual reports  (live)
    4. ownership 00000000                       # shareholders    (live)

Usage:
    python3 tools/dart-api.py search QUERY [--refresh]
    python3 tools/dart-api.py company CORP_CODE [--refresh]
    python3 tools/dart-api.py filings CORP_CODE [--type A|Q|H] [--year YYYY] [--out FILE]
    python3 tools/dart-api.py ownership CORP_CODE [--year YYYY] [--out FILE]
    python3 tools/dart-api.py preview CORP_CODE [--type A]

Examples:
    python3 tools/dart-api.py search "ExampleCorp"
    python3 tools/dart-api.py company 00000000
    python3 tools/dart-api.py filings 00000000 --type A --year 2023
    python3 tools/dart-api.py ownership 00000000 --year 2023 --out cases/example/data/corporate/examplecorp-ownership.json
"""

import argparse
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

BASE_URL = "https://opendart.fss.or.kr/api"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # conservative — free tier has daily cap

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "dart")

METADATA_TTL = 7 * 86400  # 7 days

# Optional: map a short name to a known corp_code for the companies your study
# tracks (e.g. {"example_co": "00126380"}). Empty by default.
CORP_CODES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("Error: DART_API_KEY environment variable not set.", file=sys.stderr)
        print("Register free at https://opendart.fss.or.kr/ → API key issuance.", file=sys.stderr)
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> str:
    safe = hashlib.sha256(key.encode()).hexdigest()[:16]
    readable = key.replace("/", "_").replace("?", "_").replace("&", "_")[:60]
    return os.path.join(CACHE_DIR, f"{readable}_{safe}.json")


def _cache_read(key: str, ttl: float):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) > ttl:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_write(key: str, data: str):
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


def _fetch(endpoint: str, params: dict = None, timeout: int = 120) -> str:
    params = params or {}
    params["crtfc_key"] = _get_api_key()
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{endpoint.lstrip('/')}.json"
    if query:
        url += ("&" if "?" in url else "?") + query

    _rate_limit()
    req = urllib.request.Request(url, headers={"User-Agent": "QLE-Infrastructure/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        msgs = {
            400: f"Bad request. Check parameters. URL: {url}\n{body}",
            401: "Invalid API key. Check DART_API_KEY.",
            404: f"Resource not found. URL: {url}",
            429: "Rate limited. Daily quota may be exhausted.",
            500: f"DART server error. URL: {url}",
        }
        print(f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(endpoint: str, params: dict = None, timeout: int = 120):
    raw = _fetch(endpoint, params, timeout)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse JSON response — {e}", file=sys.stderr)
        print(f"Raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
        sys.exit(1)
    # DART returns status in response body
    status = data.get("status", "000")
    if status != "000":
        msg = data.get("message", "Unknown error")
        print(f"DART API error (status {status}): {msg}", file=sys.stderr)
        if status == "010":
            print("Hint: Check DART_API_KEY is valid.", file=sys.stderr)
        elif status == "013":
            print("Hint: No data available for this query.", file=sys.stderr)
        sys.exit(1)
    return data


def _fetch_cached(key: str, endpoint: str, params: dict = None,
                  ttl: float = METADATA_TTL, refresh: bool = False) -> str:
    if not refresh:
        cached = _cache_read(key, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached
    data = _fetch(endpoint, params)
    _cache_write(key, data)
    return data


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args):
    """Search for companies by name or look up by known corp_code.

    Note: DART's company search API is limited and often returns empty for
    English names. For reliable lookups, use the 'company' command with a
    known corp_code (see --help for key corporate group codes). The search works
    best with exact Korean company names.
    """
    query = args.query

    # Check if query matches a known corporate group shorthand
    shorthand = query.lower().replace(" ", "_").replace("-", "_")
    if shorthand in CORP_CODES:
        print(f"Recognized '{query}' → corp_code {CORP_CODES[shorthand]}", file=sys.stderr)
        print(f"Use: company {CORP_CODES[shorthand]}", file=sys.stderr)
        # Fall through to also try API search

    # Try known codes by partial name match
    matches = [(k, v) for k, v in CORP_CODES.items()
               if query.lower() in k.replace("_", " ")]
    if matches:
        print(f"\nKnown corporate group matches:")
        print(f"{'NAME':<25} CORP_CODE")
        print("-" * 40)
        for name, code in matches:
            print(f"{name:<25} {code}")
        print(f"\nUse 'company CORP_CODE' for details.", file=sys.stderr)
        return

    print(f"Searching DART for '{query}'...", file=sys.stderr)
    cache_key = f"search_{query}"
    raw = _fetch_cached(cache_key, "company", {"corp_name": query}, refresh=args.refresh)
    data = json.loads(raw)

    status = data.get("status", "000")
    if status == "013":
        print(f"No companies found for '{query}'.", file=sys.stderr)
        print("Tip: DART search works best with exact Korean names. If you know a", file=sys.stderr)
        print("  company's corp_code, use the 'company CORP_CODE' command directly.", file=sys.stderr)
        return

    corps = data.get("list", [])
    print(f"\n{'CORP_CODE':<12} {'STOCK_CODE':<12} {'NAME':<40} MODIFY_DATE")
    print("-" * 100)
    for c in corps:
        code = c.get("corp_code", "")
        stock = c.get("stock_code", "—").strip() or "—"
        name = c.get("corp_name", "")
        date = c.get("modify_date", "")
        print(f"{code:<12} {stock:<12} {name:<40} {date}")
    print(f"\n{len(corps)} companies found.", file=sys.stderr)


def cmd_company(args):
    """Get company profile."""
    code = args.corp_code
    print(f"Fetching profile for {code}...", file=sys.stderr)
    cache_key = f"company_{code}"
    raw = _fetch_cached(cache_key, "company", {"corp_code": code}, refresh=args.refresh)
    data = json.loads(raw)

    status = data.get("status", "000")
    if status != "000":
        print(f"Error: {data.get('message', 'Unknown')}", file=sys.stderr)
        return

    print(f"\nCompany Profile: {code}")
    print("-" * 60)
    fields = [
        ("corp_name", "Name"),
        ("corp_name_eng", "English Name"),
        ("stock_name", "Stock Name"),
        ("stock_code", "Stock Code"),
        ("ceo_nm", "CEO"),
        ("corp_cls", "Market (Y=KOSPI, K=KOSDAQ, N=KONEX, E=ETC)"),
        ("est_dt", "Established"),
        ("acc_mt", "Fiscal Year End (month)"),
        ("induty_code", "Industry Code"),
        ("adres", "Address"),
        ("hm_url", "Website"),
    ]
    for key, label in fields:
        val = data.get(key, "")
        if val:
            print(f"  {label:<35} {val}")


def cmd_filings(args):
    """List filings for a company."""
    code = args.corp_code
    params = {"corp_code": code, "page_count": "100"}

    # bgn_de / end_de filters (date range)
    if args.year:
        params["bgn_de"] = f"{args.year}0101"
        params["end_de"] = f"{args.year}1231"

    # pblntf_ty: report type (A=annual, Q=quarterly, H=half-year)
    type_map = {"A": "A", "Q": "Q", "H": "H", "annual": "A", "quarterly": "Q"}
    if args.type:
        params["pblntf_ty"] = type_map.get(args.type.upper(), args.type)

    print(f"Fetching filings for {code}...", file=sys.stderr)
    data = _fetch_json("list", params)

    filings = data.get("list", [])
    print(f"\n{'RCV_NO':<16} {'DATE':<12} {'NAME':<60} REPORTER")
    print("-" * 120)
    for f in filings:
        rcv = f.get("rcept_no", "")
        date = f.get("rcept_dt", "")
        name = f.get("report_nm", "")[:58]
        reporter = f.get("flr_nm", "")[:20]
        print(f"{rcv:<16} {date:<12} {name:<60} {reporter}")

    total = data.get("total_count", len(filings))
    print(f"\n{total} filings total (showing {len(filings)}).", file=sys.stderr)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fout:
            json.dump(filings, fout, ensure_ascii=False, indent=2)
        print(f"Wrote {len(filings)} filings to {args.out}", file=sys.stderr)


def cmd_ownership(args):
    """Get major shareholder / cross-shareholding data."""
    code = args.corp_code
    year = args.year or "2023"
    reprt_code = "11011"  # annual report

    print(f"Fetching ownership for {code} (year {year})...", file=sys.stderr)

    # Major shareholders (5%+ holders)
    params_major = {
        "corp_code": code,
        "bsns_year": year,
        "reprt_code": reprt_code,
    }
    try:
        data_major = _fetch_json("hyslrSttus", params_major)
        holders = data_major.get("list", [])
    except SystemExit:
        holders = []
        print("  (no major shareholder data available)", file=sys.stderr)

    if holders:
        print(f"\nMajor Shareholders ({year}):")
        print(f"{'NAME':<30} {'RELATION':<20} {'SHARES':>15} {'RATIO':>10}")
        print("-" * 80)
        for h in holders:
            name = h.get("nm", "")[:28]
            rel = h.get("relate", "")[:18]
            shares = h.get("bsis_posesn_stock_co", "")
            ratio = h.get("bsis_posesn_stock_qota_rt", "")
            print(f"{name:<30} {rel:<20} {shares:>15} {ratio:>10}")

    # Executive stockholdings
    try:
        data_exec = _fetch_json("exctvSttus", params_major)
        execs = data_exec.get("list", [])
    except SystemExit:
        execs = []
        print("  (no executive data available)", file=sys.stderr)

    if execs:
        print(f"\nExecutive Officers ({year}):")
        print(f"{'NAME':<25} {'POSITION':<30} {'GENDER':<8}")
        print("-" * 65)
        for e in execs[:20]:
            name = e.get("nm", "")[:23]
            pos = e.get("ofcps", "")[:28]
            gender = e.get("sexdstn", "")
            print(f"{name:<25} {pos:<30} {gender:<8}")
        if len(execs) > 20:
            print(f"  ... +{len(execs) - 20} more")

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        result = {"year": year, "corp_code": code, "major_shareholders": holders, "executives": execs}
        with open(args.out, "w", encoding="utf-8") as fout:
            json.dump(result, fout, ensure_ascii=False, indent=2)
        print(f"Wrote ownership data to {args.out}", file=sys.stderr)


def cmd_preview(args):
    """Fetch a small sample of filing data."""
    code = args.corp_code
    params = {"corp_code": code, "page_count": "5"}
    if args.type:
        type_map = {"A": "A", "Q": "Q", "H": "H"}
        params["pblntf_ty"] = type_map.get(args.type.upper(), args.type)

    print(f"Preview filings for {code}...", file=sys.stderr)
    data = _fetch_json("list", params)
    filings = data.get("list", [])
    for f in filings[:5]:
        print(json.dumps(f, ensure_ascii=False, indent=2))
        print()
    print(f"\nQuery works. Use 'filings' for full download.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Query DART (Korean corporate disclosure) API with local cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search 'ExampleCorp'                 # find corp_code  (cached)\n"
            "  2. company 00000000                  # company profile (cached)\n"
            "  3. filings 00000000 --type A         # annual reports  (live)\n"
            "  4. ownership 00000000 --year 2023    # shareholders    (live)\n"
            "\n"
            "Key corporate group corp_codes:\n"
            "  00000000  ExampleCorp\n"
            "  00000000  a listed company\n"
            "  00000000  a listed company\n"
            "  00000000  a listed company\n"
            "  00000000  a listed company\n"
            "  00000000  a listed company\n"
            "  00000000  a listed company\n"
            "\n"
            "Requires DART_API_KEY env var (register at opendart.fss.or.kr).\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search companies by name")
    p.add_argument("query", help="Company name (Korean or English)")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # company
    p = sub.add_parser("company", help="Get company profile")
    p.add_argument("corp_code", help="DART corp_code (e.g. 00000000)")
    p.add_argument("--refresh", action="store_true")

    # filings
    p = sub.add_parser("filings", help="List filings for a company")
    p.add_argument("corp_code", help="DART corp_code")
    p.add_argument("--type", "-t", help="Report type: A(nnual), Q(uarterly), H(alf-year)")
    p.add_argument("--year", "-y", help="Filter by year (YYYY)")
    p.add_argument("--out", "-o", help="Output file path (JSON)")

    # ownership
    p = sub.add_parser("ownership", help="Get major shareholders and executives")
    p.add_argument("corp_code", help="DART corp_code")
    p.add_argument("--year", "-y", default="2023", help="Business year (default 2023)")
    p.add_argument("--out", "-o", help="Output file path (JSON)")

    # preview
    p = sub.add_parser("preview", help="Preview a small sample of filings")
    p.add_argument("corp_code", help="DART corp_code")
    p.add_argument("--type", "-t", help="Report type: A, Q, H")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "company": cmd_company,
        "filings": cmd_filings,
        "ownership": cmd_ownership,
        "preview": cmd_preview,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Query IGE (Instituto Galego de Estatística) via its JSON REST API.

All metadata is cached locally after first fetch. Only `data` queries hit
the live API on subsequent runs. Cache lives in .cache/ige/.

IGE API base: https://www.ige.gal/igebdt/igeapi/
No authentication required. Rate limiting applies for repeated requests
from single IPs — the cache prevents this in normal use.

Attribution required: "Este produto emprega a API de datos do Instituto
Galego de Estatística (IGE), pero non está certificado ou aprobado polo IGE."

This is the primary source for Examplio-level municipal data — more granular
than INE for the regionn geography. Key data: municipal population, employment
by sector and municipality, demographic indicators, economic activity.

Key datasets:
    Padrón municipal (population register) — municipal level
    Afiliados á Seguridade Social por municipio — employment by sector
    Actividade económica — GVA, sectoral indicators for the region
    Demografía — births, deaths, migration by municipality

Examplio geography:
    Municipality code: 01234
    Province: the province (15)
    CCAA: the region

Commands:
    tables      List all available tables (cached 7 days).
    search      Search tables by keyword (cached 7 days).
    preview     Fetch a small sample (5 rows) from a table.
    data        Fetch full data from a table (always live).
    indicator   Fetch an economic indicator time series.

Recommended workflow:
    1. search "poboación"                     # find table ID   (cached)
    2. preview TABLE_CODE                     # test query      (1 API call)
    3. data TABLE_CODE --out example-pop.csv  # full download   (1 API call)

Usage:
    python3 tools/ige-api.py tables [--refresh]
    python3 tools/ige-api.py search TERM [--refresh]
    python3 tools/ige-api.py preview TABLE_CODE [--geo GEO_CODE]
    python3 tools/ige-api.py data TABLE_CODE [--geo GEO_CODE] [--out FILE]
    python3 tools/ige-api.py indicator INDICATOR_CODE [--out FILE]

Examples:
    python3 tools/ige-api.py search "example"
    python3 tools/ige-api.py search "emprego"
    python3 tools/ige-api.py search "empresas"
    python3 tools/ige-api.py tables
    python3 tools/ige-api.py preview 2082
    python3 tools/ige-api.py data 2082 --out cases/example/data/demographic/population.csv
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

BASE_URL   = "https://www.ige.gal/igebdt/igeapi"
TABLES_URL = f"{BASE_URL}/taboas.jsp"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # conservative — rate limiting applies

_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "ige")

METADATA_TTL = 7 * 86400

# the region reference codes
EXAMPLE_MUNI    = "01234"
PROV_CODE    = "15"
REGION_CODE    = "12"


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
    now  = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _fetch(url: str, timeout: int = 120) -> str:
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
            400: f"Bad request. URL: {url}\n{body}",
            404: f"Table/resource not found. URL: {url}",
            429: "Rate limited by IGE. Wait before retrying.",
            500: f"IGE server error. URL: {url}",
        }
        print(f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to IGE — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key: str, url: str, ttl: float = METADATA_TTL,
                  refresh: bool = False) -> str:
    if not refresh:
        cached = _cache_read(key, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached
    data = _fetch(url)
    _cache_write(key, data)
    return data


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_tables(args):
    """List all available IGE tables."""
    print("Fetching IGE table catalog...", file=sys.stderr)
    raw = _fetch_cached("tables_all", TABLES_URL, refresh=args.refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try line-by-line
        print("Raw response (first 1000 chars):", file=sys.stderr)
        print(raw[:1000], file=sys.stderr)
        sys.exit(1)

    tables = []
    if isinstance(data, list):
        for item in data:
            tid   = str(item.get("id", item.get("codigo", item.get("Id", ""))))
            title = item.get("titulo", item.get("nombre", item.get("Nombre", "")))
            if isinstance(title, dict):
                title = title.get("es", title.get("gl", str(title)))
            tables.append((tid, str(title)))
    elif isinstance(data, dict):
        for k, v in data.items():
            tables.append((str(k), str(v)))

    tables.sort(key=lambda x: x[0].zfill(8))
    print(f"\n{'TABLE_ID':<12} TITLE")
    print("-" * 100)
    for tid, title in tables[:300]:
        print(f"{tid:<12} {title[:85]}")
    if len(tables) > 300:
        print(f"... +{len(tables) - 300} more. Use 'search' to filter.", file=sys.stderr)
    print(f"\n{len(tables)} tables total.", file=sys.stderr)
    print("Use 'search TERM' to filter by keyword.", file=sys.stderr)


def cmd_search(args):
    """Search tables by keyword."""
    term = args.term.lower()
    print(f"Searching IGE tables for '{args.term}'...", file=sys.stderr)
    raw = _fetch_cached("tables_all", TABLES_URL, refresh=args.refresh)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error parsing table catalog.", file=sys.stderr)
        sys.exit(1)

    tables = []
    if isinstance(data, list):
        for item in data:
            tid   = str(item.get("id", item.get("codigo", item.get("Id", ""))))
            title = item.get("titulo", item.get("nombre", item.get("Nombre", "")))
            if isinstance(title, dict):
                title = title.get("es", title.get("gl", str(title)))
            tables.append((tid, str(title)))

    matches = [(i, t) for i, t in tables if term in t.lower() or term in i.lower()]
    matches.sort(key=lambda x: x[0].zfill(8))

    if not matches:
        print(f"No tables matching '{args.term}'.", file=sys.stderr)
        print("Try a different keyword (the regionn or Spanish terms work).", file=sys.stderr)
        return

    print(f"\n{'TABLE_ID':<12} TITLE")
    print("-" * 100)
    for tid, title in matches:
        print(f"{tid:<12} {title[:85]}")
    print(f"\n{len(matches)} tables matching '{args.term}'.", file=sys.stderr)
    print("Use 'preview TABLE_ID' to test a query.", file=sys.stderr)


def _build_data_url(table_code: str, geo_code: str = None, fmt: str = "json") -> str:
    """Build the data URL for a table. Supports JSON and CSV formats."""
    if fmt == "csv":
        base = f"{BASE_URL}/csv/datos/{table_code}"
    else:
        base = f"{BASE_URL}/datos/{table_code}"
    if geo_code:
        base += f"/{geo_code}"
    return base


def cmd_preview(args):
    """Fetch a small sample from a table."""
    table = args.table_code
    url   = _build_data_url(table, args.geo)
    print(f"Preview table {table}...", file=sys.stderr)
    print(f"  URL: {url}", file=sys.stderr)

    raw = _fetch(url, timeout=60)

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            print(f"\n{len(data)} records in response (showing first 5):\n")
            for item in data[:5]:
                print(json.dumps(item, ensure_ascii=False, indent=2))
                print()
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    except json.JSONDecodeError:
        # Might be CSV
        lines = raw.split("\n")
        print(f"\n{len(lines)} lines in response (showing first 10):\n")
        for line in lines[:10]:
            print(line)

    print(f"\nUse 'data {table}' for full download.", file=sys.stderr)


def cmd_data(args):
    """Fetch full data from a table."""
    table = args.table_code
    fmt   = "csv" if args.csv else "json"
    url   = _build_data_url(table, args.geo, fmt=fmt)
    print(f"Fetching table {table} ({fmt})...", file=sys.stderr)

    raw = _fetch(url, timeout=300)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        n = raw.strip().count("\n")
        print(f"Wrote ~{n} rows to {args.out}", file=sys.stderr)
    else:
        print(raw)


def cmd_indicator(args):
    """Fetch an economic indicator time series."""
    code = args.indicator_code
    url  = f"{BASE_URL}/datosindi/{code}"
    print(f"Fetching indicator {code}...", file=sys.stderr)
    raw = _fetch(url, timeout=120)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"Wrote indicator data to {args.out}", file=sys.stderr)
    else:
        print(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query IGE (the regionn statistics) REST API with local cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search TERM              (cached)\n"
            "  2. preview TABLE_ID         (1 API call — sample)\n"
            "  3. data TABLE_ID --out f    (1 API call — full)\n"
            "\n"
            "Try these search terms:\n"
            "  poboación / población   municipal population\n"
            "  emprego / empleo        employment\n"
            "  empresas                firms\n"
            "  demografía              demographic indicators\n"
            "  actividade              economic activity\n"
            "\n"
            "Examplio: municipality code 01234, the province province (15), the region CCAA (12)\n"
            "\n"
            "Attribution: Este produto emprega a API de datos do IGE,\n"
            "pero non está certificado ou aprobado polo IGE.\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # tables
    p = sub.add_parser("tables", help="List all available tables")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # search
    p = sub.add_parser("search", help="Search tables by keyword")
    p.add_argument("term", help="Search keyword (the regionn or Spanish)")
    p.add_argument("--refresh", action="store_true")

    # preview
    p = sub.add_parser("preview", help="Fetch a small sample from a table")
    p.add_argument("table_code", help="IGE table code")
    p.add_argument("--geo", help="Geography filter code")

    # data
    p = sub.add_parser("data", help="Fetch full data from a table")
    p.add_argument("table_code", help="IGE table code")
    p.add_argument("--geo", help="Geography filter code")
    p.add_argument("--csv", action="store_true", help="Request CSV format (default: JSON)")
    p.add_argument("--out", "-o", help="Output file path")

    # indicator
    p = sub.add_parser("indicator", help="Fetch an economic indicator series")
    p.add_argument("indicator_code", help="IGE indicator code")
    p.add_argument("--out", "-o", help="Output file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "tables":    cmd_tables,
        "search":    cmd_search,
        "preview":   cmd_preview,
        "data":      cmd_data,
        "indicator": cmd_indicator,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

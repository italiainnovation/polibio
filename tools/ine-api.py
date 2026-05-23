#!/usr/bin/env python3
"""Query INE (Instituto Nacional de Estadística) via its JSON REST API.

All metadata is cached locally after first fetch. Only `data` queries hit
the live API on subsequent runs. Cache lives in .cache/ine/.

INE API base: https://servicios.ine.es/wstempus/js/ES/
No authentication required. No documented rate limits — use responsibly.

Key datasets (operation codes):
    30174   DIRCE — Directorio Central de Empresas (firms by municipality, CNAE, size)
    237     EPA — Encuesta de Población Activa (Labour Force Survey, quarterly)
    12747   CRE — Contabilidad Regional de España (regional GDP, GVA by sector)
    9       IPC — Consumer Price Index
    25      IPI — Índice de Producción Industrial (industrial output)
    55019   Estadística del Padrón Municipal (municipal population register)

Key table codes:
    4721    DIRCE — firms by municipality and main activity
    3996    EPA — activity rates by province
    2079    CRE — GVA at current prices by region and branch

Examplio geography:
    The municipality of Examplio has INE code 01234 (within the province province 15).
    Province codes: 15 (the province), 27 (Province B), 32 (Province C), 36 (Province D)
    CCAA the region code: 12

Commands:
    operations  List available statistical operations (cached 7 days).
    tables      List tables for an operation (cached 7 days).
    series      List series for an operation with optional filter (cached 7 days).
    metadata    Show metadata for a table (cached 7 days).
    preview     Fetch a small sample (5 obs) from a table or series.
    data        Fetch observations from a table or series (always live).

Recommended workflow:
    1. operations --search "empresas"          # find operation ID   (cached)
    2. tables 30174                            # list tables         (cached)
    3. metadata --table 4721                   # see table vars      (cached)
    4. preview --table 4721                    # test query          (1 API call)
    5. data --table 4721 --out dirce.json      # full download       (1 API call)

Usage:
    python3 tools/ine-api.py operations [--search TERM]
    python3 tools/ine-api.py tables OPERATION_ID [--search TERM]
    python3 tools/ine-api.py series OPERATION_ID [--search TERM]
    python3 tools/ine-api.py metadata --table TABLE_ID | --series SERIES_CODE
    python3 tools/ine-api.py preview --table TABLE_ID | --series SERIES_CODE [--nult N] [--tv FILTER]
    python3 tools/ine-api.py data --table TABLE_ID | --series SERIES_CODE [--nult N] [--tv FILTER] [--out FILE]

All metadata commands accept --refresh to bypass cache.

Examples:
    python3 tools/ine-api.py operations --search "empleo"
    python3 tools/ine-api.py tables 30174
    python3 tools/ine-api.py metadata --table 4721
    python3 tools/ine-api.py preview --table 4721 --nult 5
    python3 tools/ine-api.py data --table 4721 --out cases/example/data/economic/dirce.json
    python3 tools/ine-api.py data --series IPC251856 --nult 24
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

BASE_URL = "https://servicios.ine.es/wstempus/js/ES"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.5  # seconds — conservative, INE has no documented limit

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "ine")

METADATA_TTL = 7 * 86400   # 7 days
SERIES_TTL   = 7 * 86400

# Examplio / the region reference codes
EXAMPLE_CODE   = "01234"
PROV_CODE   = "15"
REGION_CODE   = "12"
REGION_PROVS  = {"15": "the province", "27": "Province B", "32": "Province C", "36": "Province D"}


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
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
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
            404: f"Resource not found. URL: {url}",
            429: "Rate limited. Wait before retrying.",
            500: f"INE server error. URL: {url}",
        }
        print(f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(endpoint: str, params: dict = None, timeout: int = 120):
    raw = _fetch(endpoint, params, timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse JSON response — {e}", file=sys.stderr)
        print(f"Raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
        sys.exit(1)


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
# Helpers
# ---------------------------------------------------------------------------


def _name(obj) -> str:
    """Extract name string from INE name objects (may be string, dict, or list)."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("Es", obj.get("en", str(obj)))
    if isinstance(obj, list) and obj:
        return _name(obj[0])
    return str(obj)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_operations(args):
    """List available statistical operations."""
    print("Fetching INE operations catalog...", file=sys.stderr)
    cache_key = "operations_all"
    raw = _fetch_cached(cache_key, "OPERACIONES_DISPONIBLES", refresh=args.refresh)
    data = json.loads(raw)

    ops = []
    for op in data:
        op_id   = str(op.get("Id", ""))
        code    = op.get("Codigo", "")
        name    = _name(op.get("Nombre", ""))
        ops.append((op_id, code, name))

    if args.search:
        t = args.search.lower()
        ops = [(i, c, n) for i, c, n in ops if t in n.lower() or t in c.lower() or t in i]

    ops.sort(key=lambda x: x[0].zfill(8))
    print(f"\n{'ID':<8} {'CODE':<20} NAME")
    print("-" * 100)
    for op_id, code, name in ops:
        print(f"{op_id:<8} {code:<20} {name[:70]}")
    print(f"\n{len(ops)} operations.", file=sys.stderr)


def cmd_tables(args):
    """List tables for a statistical operation."""
    op_id = args.operation_id
    print(f"Fetching tables for operation {op_id}...", file=sys.stderr)
    cache_key = f"tables_{op_id}"
    raw = _fetch_cached(cache_key, f"TABLAS_OPERACION/{op_id}", refresh=args.refresh)
    data = json.loads(raw)

    tables = []
    for t in data:
        tid  = str(t.get("Id", ""))
        name = _name(t.get("Nombre", ""))
        tables.append((tid, name))

    if args.search:
        q = args.search.lower()
        tables = [(i, n) for i, n in tables if q in n.lower() or q in i]

    tables.sort(key=lambda x: x[0].zfill(8))
    print(f"\n{'TABLE_ID':<12} NAME")
    print("-" * 100)
    for tid, name in tables:
        print(f"{tid:<12} {name[:85]}")
    print(f"\n{len(tables)} tables.", file=sys.stderr)
    print("Use 'metadata --table TABLE_ID' to see variables.", file=sys.stderr)


def cmd_series(args):
    """List series for an operation with optional filter."""
    op_id = args.operation_id
    print(f"Fetching series for operation {op_id}...", file=sys.stderr)
    params = {}
    if args.search:
        params["det"] = "1"
    cache_key = f"series_{op_id}_{args.search or 'all'}"
    raw = _fetch_cached(cache_key, f"SERIES_OPERACION/{op_id}", params=params,
                        refresh=args.refresh)
    data = json.loads(raw)

    series = []
    for s in (data if isinstance(data, list) else []):
        sid   = str(s.get("COD", s.get("Cod", "")))
        name  = _name(s.get("Nombre", s.get("nombre", "")))
        series.append((sid, name))

    if args.search:
        q = args.search.lower()
        series = [(i, n) for i, n in series if q in n.lower() or q in i.lower()]

    print(f"\n{'SERIES_CODE':<25} NAME")
    print("-" * 100)
    for sid, name in series[:200]:
        print(f"{sid:<25} {name[:75]}")
    if len(series) > 200:
        print(f"... +{len(series) - 200} more. Narrow with --search.", file=sys.stderr)
    print(f"\n{len(series)} series.", file=sys.stderr)


def cmd_metadata(args):
    """Show metadata for a table or series."""
    if args.table:
        tid = args.table
        print(f"Metadata for table {tid}...", file=sys.stderr)
        cache_key = f"meta_table_{tid}"
        raw = _fetch_cached(cache_key, f"DATOS_TABLA/{tid}",
                            params={"tip": "M"}, refresh=args.refresh)
        data = json.loads(raw)
        # Table metadata varies in structure; print what we have
        if isinstance(data, list):
            print(f"\nTable {tid} — {len(data)} series/variables")
            for item in data[:20]:
                name = _name(item.get("Nombre", item.get("nombre", item.get("COD", ""))))
                print(f"  {name[:90]}")
            if len(data) > 20:
                print(f"  ... +{len(data) - 20} more")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
    elif args.series:
        code = args.series
        print(f"Metadata for series {code}...", file=sys.stderr)
        cache_key = f"meta_series_{code}"
        raw = _fetch_cached(cache_key, f"SERIE/{code}", refresh=args.refresh)
        data = json.loads(raw)
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    else:
        print("Error: provide --table TABLE_ID or --series SERIES_CODE", file=sys.stderr)
        sys.exit(1)


def cmd_preview(args):
    """Fetch a small sample to test a query."""
    params = {"tip": "AM", "nult": str(args.nult)}
    if args.tv:
        params["tv"] = args.tv

    if args.table:
        endpoint = f"DATOS_TABLA/{args.table}"
        label = f"table {args.table}"
    elif args.series:
        endpoint = f"DATOS_SERIE/{args.series}"
        label = f"series {args.series}"
    else:
        print("Error: provide --table TABLE_ID or --series SERIES_CODE", file=sys.stderr)
        sys.exit(1)

    print(f"Preview {label}...", file=sys.stderr)
    data = _fetch_json(endpoint, params)

    if isinstance(data, list):
        print(f"\n{len(data)} items in response (showing first {min(5, len(data))}):\n")
        for item in data[:5]:
            print(json.dumps(item, ensure_ascii=False, indent=2))
            print()
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])

    print(f"\nQuery works. Use 'data' with same parameters for full download.", file=sys.stderr)


def cmd_data(args):
    """Fetch full data from a table or series."""
    params = {"tip": "AM"}
    if args.nult:
        params["nult"] = str(args.nult)
    if args.tv:
        params["tv"] = args.tv

    if args.table:
        endpoint = f"DATOS_TABLA/{args.table}"
        label = f"table {args.table}"
    elif args.series:
        endpoint = f"DATOS_SERIE/{args.series}"
        label = f"series {args.series}"
    else:
        print("Error: provide --table TABLE_ID or --series SERIES_CODE", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching {label}...", file=sys.stderr)
    raw = _fetch(endpoint, params, timeout=300)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        try:
            n = len(json.loads(raw))
            print(f"Wrote {n} records to {args.out}", file=sys.stderr)
        except Exception:
            print(f"Wrote data to {args.out}", file=sys.stderr)
    else:
        print(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query INE (Spain national statistics) JSON REST API with local cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. operations --search TOPIC       (cached)\n"
            "  2. tables OPERATION_ID             (cached)\n"
            "  3. metadata --table TABLE_ID       (cached)\n"
            "  4. preview --table TABLE_ID        (1 API call — sample)\n"
            "  5. data --table TABLE_ID --out f   (1 API call — full)\n"
            "\n"
            "Key operation IDs:\n"
            "  30174  DIRCE — firm demographics by municipality/CNAE\n"
            "  237    EPA — Labour Force Survey (quarterly)\n"
            "  12747  CRE — Regional GDP and GVA\n"
            "  25     IPI — Industrial Production Index\n"
            "  55019  Padrón Municipal — municipal population\n"
            "\n"
            "Key table IDs:\n"
            "  4721   DIRCE firms by municipality and activity\n"
            "\n"
            "Examplio: municipality code 01234, province the province (15), CCAA the region (12)\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # operations
    p = sub.add_parser("operations", help="List statistical operations")
    p.add_argument("--search", "-s", help="Filter by keyword")
    p.add_argument("--refresh", action="store_true", help="Bypass cache")

    # tables
    p = sub.add_parser("tables", help="List tables for an operation")
    p.add_argument("operation_id", help="Operation ID (e.g. 30174 for DIRCE)")
    p.add_argument("--search", "-s", help="Filter tables by keyword")
    p.add_argument("--refresh", action="store_true")

    # series
    p = sub.add_parser("series", help="List series for an operation")
    p.add_argument("operation_id", help="Operation ID")
    p.add_argument("--search", "-s", help="Filter series by keyword")
    p.add_argument("--refresh", action="store_true")

    # metadata
    p = sub.add_parser("metadata", help="Show metadata for a table or series")
    p.add_argument("--table", "-t", help="Table ID")
    p.add_argument("--series", "-s", help="Series code")
    p.add_argument("--refresh", action="store_true")

    # preview
    p = sub.add_parser("preview", help="Fetch a small sample (test query)")
    p.add_argument("--table", "-t", help="Table ID")
    p.add_argument("--series", "-s", help="Series code")
    p.add_argument("--nult", "-n", type=int, default=5, help="Last N periods (default 5)")
    p.add_argument("--tv", help="Filter by variable value (format: varId:valueId)")

    # data
    p = sub.add_parser("data", help="Fetch full data")
    p.add_argument("--table", "-t", help="Table ID")
    p.add_argument("--series", "-s", help="Series code")
    p.add_argument("--nult", "-n", type=int, help="Last N periods")
    p.add_argument("--tv", help="Filter by variable value (format: varId:valueId)")
    p.add_argument("--out", "-o", help="Output file path (JSON)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "operations": cmd_operations,
        "tables":     cmd_tables,
        "series":     cmd_series,
        "metadata":   cmd_metadata,
        "preview":    cmd_preview,
        "data":       cmd_data,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

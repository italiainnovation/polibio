#!/usr/bin/env python3
"""Query Seguridad Social (Spanish Social Security) employment affiliation data
via the PX-Web standard API.

PX-Web base: https://w6.seg-social.es/PXWeb/api/v1/es/
No authentication required. Max ~120,000 cells per query.
Cache lives in .cache/seguridad-social/.

This is the **key dataset for measuring employment concentration** in Examplio:
  - Number of workers affiliated (insured) by municipality and CNAE sector
  - Available from 2011 onwards, monthly, at municipal level
  - Disaggregated by regime (general, self-employed, agricultural, sea)

The critical table for Examplio:
  Afiliados por Municipios de las personas trabajadoras afiliadas en Alta
  Laboral por actividad económica (CNAE-2009) a 2 dígitos.
  → municipal employment by 2-digit CNAE code (industry sector)

PX-Web API protocol:
  GET  /api/v1/es/                           list databases
  GET  /api/v1/es/{db}/                      list tables in database
  GET  /api/v1/es/{db}/{table}.px            table metadata (dimensions + codes)
  POST /api/v1/es/{db}/{table}.px            query data

  POST body (JSON):
  {
    "query": [
      {"code": "DIMENSION", "selection": {"filter": "item", "values": ["val1"]}}
    ],
    "response": {"format": "json-stat"}
  }

  Response format: json-stat (standard statistical data format)

Commands:
    databases   List available databases (cached 7 days).
    tables      List tables in a database (cached 7 days).
    metadata    Show dimensions and codes for a table (cached 7 days).
    preview     Fetch a small sample from a table (5 observations).
    data        Fetch data from a table with dimension filters.

Recommended workflow:
    1. databases                                    # find database name
    2. tables SS_AFILIADOS                          # find table path
    3. metadata SS_AFILIADOS/some_table.px          # see dimensions and codes
    4. preview SS_AFILIADOS/table.px --muni 01234   # test for Examplio
    5. data SS_AFILIADOS/table.px --muni 01234 --out example-employment.csv

Usage:
    python3 tools/seguridad-social-api.py databases [--refresh]
    python3 tools/seguridad-social-api.py tables DATABASE [--search TERM] [--refresh]
    python3 tools/seguridad-social-api.py metadata DATABASE/TABLE.px [--refresh]
    python3 tools/seguridad-social-api.py preview DATABASE/TABLE.px [--muni CODE] [--cnae CODE]
    python3 tools/seguridad-social-api.py data DATABASE/TABLE.px [--muni CODE] [--cnae CODE] [--year YYYY] [--out FILE]

Examples:
    python3 tools/seguridad-social-api.py databases
    python3 tools/seguridad-social-api.py tables Series_afiliados --search "municipio"
    python3 tools/seguridad-social-api.py metadata Series_afiliados/afimuni_cnae2.px
    python3 tools/seguridad-social-api.py preview Series_afiliados/afimuni_cnae2.px --muni 01234
    python3 tools/seguridad-social-api.py data Series_afiliados/afimuni_cnae2.px --muni 01234 --out cases/example/data/economic/employment-by-sector.csv
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

BASE_URL = "https://w6.seg-social.es/PXWeb/api/v1/es"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # conservative

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR     = os.path.join(_PROJECT_ROOT, ".cache", "seguridad-social")

METADATA_TTL = 7 * 86400

# Examplio reference codes
EXAMPLE_MUNI = "01234"
PROV_CODE = "15"

# Known table paths (discovered from the PX-Web catalog)
KNOWN_TABLES = {
    "afimuni_cnae2": "Afiliados por municipio y CNAE-2009 (2 dígitos)",
    "afimuniSS":     "Afiliados por municipio y régimen de Seguridad Social",
    "afimuniSexo":   "Afiliados por municipio y sexo",
}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(key: str) -> str:
    safe     = hashlib.sha256(key.encode()).hexdigest()[:16]
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


def _get(path: str, timeout: int = 60) -> str:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    _rate_limit()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "QLE-Infrastructure/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        print(f"Error: HTTP {e.code} — {url}\n{body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to Seguridad Social API — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _post(path: str, body: dict, timeout: int = 120) -> str:
    url  = f"{BASE_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8")
    _rate_limit()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent":   "QLE-Infrastructure/1.0",
            "Accept":       "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_err = ""
        try:
            body_err = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        print(f"Error: HTTP {e.code} — {url}\n{body_err}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _get_cached(key: str, path: str, ttl: float = METADATA_TTL,
                refresh: bool = False) -> str:
    if not refresh:
        cached = _cache_read(key, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached
    data = _get(path)
    _cache_write(key, data)
    return data


# ---------------------------------------------------------------------------
# JSON-stat parsing
# ---------------------------------------------------------------------------


def _jsonstat_to_rows(raw: str) -> list:
    """Convert json-stat response to a flat list of dicts."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    # json-stat v2 format
    if "dataset" in data:
        data = data["dataset"]

    dimensions = data.get("dimension", {})
    values     = data.get("value", [])
    ids        = data.get("id", list(dimensions.keys()))
    sizes      = data.get("size", [])

    if not dimensions or not values:
        return []

    # Build label maps for each dimension
    label_maps = {}
    for dim_id in ids:
        dim = dimensions.get(dim_id, {})
        cats = dim.get("category", {})
        label = cats.get("label", {})
        index = cats.get("index", {})
        # index is either dict {code: position} or list
        if isinstance(index, list):
            code_order = index
        else:
            code_order = sorted(index, key=lambda k: index[k])
        label_maps[dim_id] = [(c, label.get(c, c)) for c in code_order]

    # Iterate over all combinations
    rows = []
    n_values = len(values)
    n_dims   = len(ids)

    def _iterate(pos, dim_idx, current):
        nonlocal rows
        if dim_idx == n_dims:
            if pos < n_values:
                row = dict(current)
                row["value"] = values[pos]
                rows.append(row)
            return
        dim_id = ids[dim_idx]
        codes  = label_maps[dim_id]
        stride = 1
        for k in range(dim_idx + 1, n_dims):
            stride *= sizes[k] if k < len(sizes) else 1
        for i, (code, label_val) in enumerate(codes):
            current[dim_id]             = code
            current[f"{dim_id}_label"]  = label_val
            _iterate(pos + i * stride, dim_idx + 1, current)

    _iterate(0, 0, {})
    return rows


def _write_csv(rows: list, out_path: str = None):
    if not rows:
        print("No data rows returned.", file=sys.stderr)
        return
    fields = list(rows[0].keys())
    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {out_path}", file=sys.stderr)
    else:
        buf = io.StringIO()
        w   = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        print(buf.getvalue())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_databases(args):
    """List available PX-Web databases."""
    print("Fetching Seguridad Social databases...", file=sys.stderr)
    raw  = _get_cached("databases", "", ttl=METADATA_TTL, refresh=args.refresh)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw[:2000])
        return

    items = data if isinstance(data, list) else data.get("databases", [data])
    print(f"\n{'DATABASE_ID':<30} DESCRIPTION")
    print("-" * 80)
    for item in items:
        db_id = item.get("id", item.get("dbid", ""))
        desc  = item.get("text", item.get("description", ""))
        print(f"{db_id:<30} {desc[:55]}")
    print(f"\n{len(items)} databases.", file=sys.stderr)
    print("Use 'tables DATABASE_ID' to list tables.", file=sys.stderr)


def cmd_tables(args):
    """List tables in a database."""
    db = args.database
    print(f"Fetching tables in {db}...", file=sys.stderr)
    raw = _get_cached(f"tables_{db}", f"{db}/", ttl=METADATA_TTL, refresh=args.refresh)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw[:2000])
        return

    tables = data if isinstance(data, list) else []
    if args.search:
        q = args.search.lower()
        tables = [t for t in tables
                  if q in t.get("text", "").lower() or q in t.get("id", "").lower()]

    print(f"\n{'TABLE_PATH':<40} DESCRIPTION")
    print("-" * 100)
    for t in tables:
        tid  = t.get("id", "")
        text = t.get("text", "")
        print(f"{tid:<40} {text[:55]}")
    print(f"\n{len(tables)} tables.", file=sys.stderr)
    print("Use 'metadata DB/TABLE.px' to see dimensions.", file=sys.stderr)


def cmd_metadata(args):
    """Show dimensions and codes for a table."""
    table_path = args.table_path
    print(f"Fetching metadata for {table_path}...", file=sys.stderr)

    cache_key = f"meta_{table_path.replace('/', '_')}"
    raw = _get_cached(cache_key, f"{table_path}", ttl=METADATA_TTL, refresh=args.refresh)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw[:3000])
        return

    variables = data.get("variables", [])
    print(f"\nTable: {table_path}")
    print(f"Title: {data.get('title', '')}")
    print(f"\n{len(variables)} dimensions:\n")

    for var in variables:
        var_code  = var.get("code", "")
        var_text  = var.get("text", "")
        values    = var.get("values", [])
        val_texts = var.get("valueTexts", values)
        elimination = var.get("elimination", False)
        print(f"  {var_code:<20} {var_text[:50]}")
        print(f"    {len(values)} values | elimination={elimination}")
        # Show first 10 values
        for v, t in list(zip(values, val_texts))[:10]:
            print(f"    {v:<15} {t}")
        if len(values) > 10:
            print(f"    ... +{len(values) - 10} more values")
        print()


def _build_query(metadata_raw: str, muni: str = None, cnae: str = None,
                 year: str = None, preview: bool = False) -> dict:
    """Build a PX-Web query body from metadata and filters."""
    try:
        meta = json.loads(metadata_raw)
    except json.JSONDecodeError:
        return {"query": [], "response": {"format": "json-stat"}}

    variables = meta.get("variables", [])
    query = []

    for var in variables:
        code    = var.get("code", "")
        values  = var.get("values", [])
        elim    = var.get("elimination", False)

        selected = None

        # Municipality filter
        if muni and any(k in code.lower() for k in ["muni", "municipio", "localidad"]):
            matching = [v for v in values if muni in v]
            if matching:
                selected = matching[:1]

        # CNAE filter
        elif cnae and any(k in code.lower() for k in ["cnae", "actividad", "sector"]):
            matching = [v for v in values if v.startswith(cnae)]
            if matching:
                selected = matching

        # Year filter
        elif year and any(k in code.lower() for k in ["año", "year", "periodo", "tiempo"]):
            matching = [v for v in values if year in v]
            if matching:
                selected = matching if not preview else matching[:3]

        # For preview: take first value of other dimensions
        if selected is None:
            if preview:
                selected = values[:1] if values else []
            elif elim:
                selected = values  # include all if elimination is available
            else:
                selected = values  # include all

        if selected:
            query.append({
                "code":      code,
                "selection": {"filter": "item", "values": selected},
            })

    return {
        "query":    query,
        "response": {"format": "json-stat"},
    }


def cmd_preview(args):
    """Fetch a small sample from a table."""
    table_path = args.table_path
    print(f"Preview {table_path}...", file=sys.stderr)

    # First fetch metadata
    cache_key = f"meta_{table_path.replace('/', '_')}"
    meta_raw  = _get_cached(cache_key, table_path, ttl=METADATA_TTL)

    body = _build_query(meta_raw, muni=args.muni, cnae=args.cnae, preview=True)
    print(f"  Query: {json.dumps(body, ensure_ascii=False)[:400]}", file=sys.stderr)

    raw  = _post(table_path, body)
    rows = _jsonstat_to_rows(raw)

    print(f"\n{len(rows)} sample rows (showing first 10):\n")
    if rows:
        print(", ".join(rows[0].keys()))
        print("-" * 80)
        for row in rows[:10]:
            print(", ".join(str(v) for v in row.values()))
    print(f"\nUse 'data {table_path}' for full download.", file=sys.stderr)


def cmd_data(args):
    """Fetch full data from a table."""
    table_path = args.table_path
    print(f"Fetching {table_path}...", file=sys.stderr)

    # Fetch metadata first
    cache_key = f"meta_{table_path.replace('/', '_')}"
    meta_raw  = _get_cached(cache_key, table_path, ttl=METADATA_TTL)

    body = _build_query(meta_raw, muni=args.muni, cnae=args.cnae, year=args.year)
    print(f"  Query: {json.dumps(body, ensure_ascii=False)[:600]}", file=sys.stderr)

    raw  = _post(table_path, body, timeout=300)
    rows = _jsonstat_to_rows(raw)

    print(f"  {len(rows)} rows retrieved.", file=sys.stderr)
    _write_csv(rows, args.out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query Seguridad Social employment affiliation data via PX-Web API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key use case: employment concentration in Examplio by sector\n"
            "\n"
            "Recommended workflow:\n"
            "  1. databases                                  (cached)\n"
            "  2. tables Series_afiliados --search municipio (cached)\n"
            "  3. metadata Series_afiliados/afimuni_cnae2.px (cached)\n"
            "  4. preview  Series_afiliados/afimuni_cnae2.px --muni 01234\n"
            "  5. data     Series_afiliados/afimuni_cnae2.px --muni 01234 --out example.csv\n"
            "\n"
            "Examplio municipality code: 01234\n"
            "the province province code: 15\n"
            "Key CNAE sectors for SampleCorp: 14 (textile), 52 (retail trade warehousing)\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # databases
    p = sub.add_parser("databases", help="List available databases")
    p.add_argument("--refresh", action="store_true")

    # tables
    p = sub.add_parser("tables", help="List tables in a database")
    p.add_argument("database", help="Database ID (e.g. Series_afiliados)")
    p.add_argument("--search", "-s", help="Filter tables by keyword")
    p.add_argument("--refresh", action="store_true")

    # metadata
    p = sub.add_parser("metadata", help="Show dimensions and codes for a table")
    p.add_argument("table_path", help="DB/table.px path")
    p.add_argument("--refresh", action="store_true")

    # preview
    p = sub.add_parser("preview", help="Fetch a small sample from a table")
    p.add_argument("table_path", help="DB/table.px path")
    p.add_argument("--muni",  help="Municipality code (e.g. 01234 for Examplio)")
    p.add_argument("--cnae",  help="CNAE sector code prefix (e.g. 14 for textile)")

    # data
    p = sub.add_parser("data", help="Fetch full data from a table")
    p.add_argument("table_path", help="DB/table.px path")
    p.add_argument("--muni",  help="Municipality code (e.g. 01234 for Examplio)")
    p.add_argument("--cnae",  help="CNAE sector code prefix")
    p.add_argument("--year",  help="Year filter (e.g. 2023)")
    p.add_argument("--out",  "-o", help="Output CSV file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "databases": cmd_databases,
        "tables":    cmd_tables,
        "metadata":  cmd_metadata,
        "preview":   cmd_preview,
        "data":      cmd_data,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

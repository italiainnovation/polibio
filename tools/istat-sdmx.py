#!/usr/bin/env python3
"""Query ISTAT (Italian National Institute of Statistics) via SDMX REST API.

All metadata is cached locally after first fetch. Only `data` queries hit
the live API on subsequent runs. Cache lives in .cache/istat-sdmx/.

ISTAT rate limit: 5 queries/minute per IP. Violations trigger 1-2 day
IP blocks. This tool enforces 13s between API requests and caches
aggressively so typical sessions use 1-3 live API calls total.

Commands:
    list        Search the dataflow catalog (cached 7 days).
    structure   Show dimensions and codelist previews (cached 30 days).
    available   Show which codelist values actually exist in a dataflow
                (cached 30 days). ISTAT's codelists are shared across
                dataflows — not every code is valid everywhere. Always
                check this before constructing a data query.
    codelist    Show full codelist for a dimension (cached, from structure).
    preview     Fetch a small sample from a dataflow (5 obs) to verify
                your key works before committing to a full download.
    data        Fetch observations (always live).

Recommended workflow (costs 1-2 API calls after first run):
    1. list --search "topic"           # find dataflow ID     (cached)
    2. structure DATAFLOW_ID           # see dimensions       (cached)
    3. available DATAFLOW_ID           # valid codes only     (cached)
    4. codelist DATAFLOW_ID DIM -f kw  # browse a dimension   (cached)
    5. preview DATAFLOW_ID KEY         # test with 5 obs      (1 API call)
    6. data DATAFLOW_ID KEY --out f    # full download         (1 API call)

Usage:
    python3 tools/istat-sdmx.py list [--search TERM]
    python3 tools/istat-sdmx.py structure DATAFLOW_ID
    python3 tools/istat-sdmx.py available DATAFLOW_ID
    python3 tools/istat-sdmx.py codelist DATAFLOW_ID DIMENSION_ID [-f TERM]
    python3 tools/istat-sdmx.py preview DATAFLOW_ID [KEY]
    python3 tools/istat-sdmx.py data DATAFLOW_ID [KEY] [--start Y] [--end Y] [--last N] [--out FILE]

All metadata commands accept --refresh to bypass cache.

Examples:
    python3 tools/istat-sdmx.py list --search "labour force"
    python3 tools/istat-sdmx.py structure 150_908
    python3 tools/istat-sdmx.py available 150_908
    python3 tools/istat-sdmx.py codelist 150_908 REF_AREA -f "emilia"
    python3 tools/istat-sdmx.py preview 150_908 A.ITD5
    python3 tools/istat-sdmx.py data 150_908 A.ITD5 --start 2015 --end 2023 --out labour.csv
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import socket
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENDPOINTS = [
    "https://esploradati.istat.it/SDMXWS/rest",
    "https://sdmx.istat.it/SDMXWS/rest",
]
AGENCY = "IT1"

NS = {
    "mes": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 13.0  # seconds — keeps us well under 5/min

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "istat-sdmx")

CATALOG_TTL = 7 * 86400     # 7 days
STRUCTURE_TTL = 30 * 86400  # 30 days


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(key):
    safe = hashlib.sha256(key.encode()).hexdigest()[:16]
    readable = key.replace("/", "_").replace("?", "_")[:60]
    return os.path.join(CACHE_DIR, f"{readable}_{safe}")


def _cache_read(key, ttl):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) > ttl:
        return None
    with open(path, "rb") as f:
        return f.read()


def _cache_write(key, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_active_endpoint = None
_BASE_URL = None


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        print(f"  Rate limit: waiting {wait:.0f}s...", file=sys.stderr)
        time.sleep(wait)
    _last_request_time = time.time()


def _select_endpoint():
    global _active_endpoint, _BASE_URL
    if _active_endpoint:
        return _active_endpoint

    ep_cache = os.path.join(CACHE_DIR, "_endpoint")
    if os.path.exists(ep_cache):
        age = time.time() - os.path.getmtime(ep_cache)
        if age < 3600:
            with open(ep_cache) as f:
                ep = f.read().strip()
            if ep in ENDPOINTS:
                _active_endpoint = ep
                _BASE_URL = ep
                return ep

    for ep in ENDPOINTS:
        try:
            req = urllib.request.Request(
                f"{ep}/dataflow/{AGENCY}/EMP",
                headers={"User-Agent": "QLE-Infrastructure/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status < 500:
                    _active_endpoint = ep
                    _BASE_URL = ep
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(ep_cache, "w") as f:
                        f.write(ep)
                    print(f"Endpoint: {ep}", file=sys.stderr)
                    return ep
        except Exception:
            continue

    _active_endpoint = ENDPOINTS[0]
    _BASE_URL = ENDPOINTS[0]
    print(
        "Warning: No ISTAT endpoint responded. You may be rate-limited "
        "(blocks last 1-2 days) or the service is down.",
        file=sys.stderr,
    )
    return _active_endpoint


def _fetch_live(path, accept=None, timeout=60):
    _select_endpoint()
    url = f"{_BASE_URL}/{path.lstrip('/')}"
    headers = {"User-Agent": "QLE-Infrastructure/1.0"}
    if accept:
        headers["Accept"] = accept

    _rate_limit()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        msgs = {
            404: f"Not found — {url}",
            422: (
                f"Invalid query (422). Dimension key may be wrong or too "
                f"broad. Use 'available' to check valid codes. URL: {url}"
            ),
            429: "Rate limited by ISTAT. Wait before retrying.",
            500: f"ISTAT server error (500). URL: {url}",
        }
        print(
            f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)
    except socket.timeout:
        print(
            f"Error: Request timed out ({timeout}s). The response may be "
            f"too large. Try a more specific query or increase timeout.",
            file=sys.stderr,
        )
        sys.exit(1)


def _fetch_cached_xml(path, ttl, refresh=False, timeout=120):
    if not refresh:
        cached = _cache_read(path, ttl)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return ET.fromstring(cached)
    data = _fetch_live(path, timeout=timeout)
    _cache_write(path, data)
    return ET.fromstring(data)


def _fetch_csv(path, timeout=120):
    accept = "application/vnd.sdmx.data+csv;version=1.0.0"
    data = _fetch_live(path, accept=accept, timeout=timeout)
    return data.decode("utf-8")


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def _extract_name(el):
    en = it = ""
    for n in el.iter(f"{{{NS['com']}}}Name"):
        lang = n.get("{http://www.w3.org/XML/1998/namespace}lang", "")
        if lang == "en":
            en = n.text or ""
        elif lang == "it":
            it = n.text or ""
    return en or it or "(no name)"


def _parse_dimensions(root):
    dims = []
    for dim in root.iter(f"{{{NS['str']}}}Dimension"):
        did = dim.get("id", "")
        if not did:
            continue
        pos = dim.get("position", "")
        concept = codelist = ""
        for ref in dim.iter("Ref"):
            cls = ref.get("class", "")
            if cls == "Concept":
                concept = ref.get("id", "")
            elif cls == "Codelist":
                codelist = ref.get("id", "")
        dims.append((pos, did, concept, codelist))
    for td in root.iter(f"{{{NS['str']}}}TimeDimension"):
        dims.append(("T", td.get("id", "TIME_PERIOD"), "TIME_PERIOD", ""))
    dims.sort(key=lambda x: (x[0] if x[0] != "T" else "999"))
    return dims


def _parse_codelists(root):
    cls = {}
    for cl in root.iter(f"{{{NS['str']}}}Codelist"):
        cl_id = cl.get("id", "")
        codes = []
        for c in cl.iter(f"{{{NS['str']}}}Code"):
            name = _extract_name(c)
            codes.append((c.get("id", ""), "" if name == "(no name)" else name))
        if codes:
            cls[cl_id] = codes
    return cls


def _get_structure(df_id, refresh=False):
    """Fetch and cache the full structure+codelists for a dataflow."""
    path = f"dataflow/{AGENCY}/{df_id}?references=all"
    return _fetch_cached_xml(path, STRUCTURE_TTL, refresh=refresh, timeout=240)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args):
    print("Fetching ISTAT dataflow catalog...", file=sys.stderr)
    path = f"dataflow/{AGENCY}/all/latest?detail=allstubs"
    root = _fetch_cached_xml(path, CATALOG_TTL, refresh=args.refresh, timeout=180)

    dfs = []
    for df in root.iter(f"{{{NS['str']}}}Dataflow"):
        did = df.get("id", "")
        if did:
            dfs.append((did, _extract_name(df)))

    if args.search:
        t = args.search.lower()
        dfs = [(d, n) for d, n in dfs if t in d.lower() or t in n.lower()]

    if not dfs:
        print("No dataflows found.", file=sys.stderr)
        return

    dfs.sort()
    print(f"\n{'ID':<40} NAME")
    print("-" * 100)
    for did, name in dfs:
        print(f"{did:<40} {name[:70]}{'...' if len(name)>70 else ''}")
    print(f"\n{len(dfs)} dataflows.", file=sys.stderr)


def cmd_structure(args):
    df_id = args.dataflow_id
    print(f"Structure of {df_id}...", file=sys.stderr)
    root = _get_structure(df_id, refresh=args.refresh)
    dims = _parse_dimensions(root)

    if not dims:
        print("No dimensions found.", file=sys.stderr)
        return

    print(f"\nDataflow: {df_id}")
    print(f"\n{'POS':<5} {'DIMENSION':<30} {'CODELIST'}")
    print("-" * 75)
    for pos, did, _, cl in dims:
        print(f"{pos:<5} {did:<30} {cl}")

    print(
        "\nKey: dots separate dimensions in position order. "
        "'.' = wildcard, '+' = OR.",
        file=sys.stderr,
    )

    codelists = _parse_codelists(root)
    if codelists:
        print("\n--- Codelist previews (first 10) ---\n")
        for _, did, _, cl_id in dims:
            if cl_id and cl_id in codelists:
                codes = codelists[cl_id]
                print(f"  {did} ({len(codes)} values):")
                for cid, cn in codes[:10]:
                    print(f"    {cid}{f' — {cn}' if cn else ''}")
                if len(codes) > 10:
                    print(f"    ... +{len(codes)-10} more → codelist {df_id} {did}")
                print()


def cmd_available(args):
    """Show which dimension values actually exist in a dataflow.

    ISTAT codelists are shared across dataflows. A codelist may have 11,000
    territory codes but a specific dataflow may only contain data for 200 of
    them. This command discovers which values actually exist by fetching
    series keys (no observation data) and extracting the distinct dimension
    values present.

    Uses detail=serieskeysonly which ISTAT supports reliably (unlike the
    availableconstraint endpoint which returns 500 on many dataflows).
    """
    df_id = args.dataflow_id
    print(f"Discovering available values for {df_id}...", file=sys.stderr)

    # Fetch one observation per series — gives us every dimension combination
    # that actually exists. serieskeysonly returns empty CSV on ISTAT's
    # .Stat implementation, so we use lastNObservations=1 instead.
    cache_key = f"available_{df_id}"
    cached = None if args.refresh else _cache_read(cache_key, STRUCTURE_TTL)

    if cached:
        print("  (from cache)", file=sys.stderr)
        csv_text = cached.decode("utf-8")
    else:
        accept = "application/vnd.sdmx.data+csv;version=1.0.0"
        path = f"data/{df_id}?lastNObservations=1"
        data = _fetch_live(path, accept=accept, timeout=300)
        csv_text = data.decode("utf-8")
        _cache_write(cache_key, data)

    # Parse CSV to extract unique values per dimension
    lines = csv_text.strip().split("\n")
    if len(lines) < 2:
        print("No series found in this dataflow.", file=sys.stderr)
        return

    headers = lines[0].split(",")

    # Find dimension columns (exclude DATAFLOW, TIME_PERIOD, OBS_VALUE,
    # and attribute columns which typically come after OBS_VALUE)
    skip = {
        "DATAFLOW", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS",
        "COMMENT", "BASE_PER", "UNIT_MEAS", "UNIT_MULT", "TIME_FORMAT",
        "NOTE_DS",
    }
    # Also skip any column starting with NOTE_
    dim_cols = []
    for i, h in enumerate(headers):
        h_clean = h.strip()
        if h_clean not in skip and not h_clean.startswith("NOTE_"):
            dim_cols.append((i, h_clean))

    # Collect distinct values per dimension
    dim_values = {h: set() for _, h in dim_cols}
    for line in lines[1:]:
        fields = line.split(",")
        for i, h in dim_cols:
            if i < len(fields) and fields[i].strip():
                dim_values[h].add(fields[i].strip())

    # Get structure for context (from cache)
    struct_root = _get_structure(df_id, refresh=False)
    codelists = _parse_codelists(struct_root)
    dims = _parse_dimensions(struct_root)

    # Build a codelist lookup: dimension_id → {code: name}
    dim_names = {}
    for _, did, _, cl_id in dims:
        if cl_id and cl_id in codelists:
            dim_names[did] = {c: n for c, n in codelists[cl_id]}

    print(f"\nAvailable values in {df_id} ({len(lines)-1} series):\n")
    for _, dim_id in dim_cols:
        vals = sorted(dim_values.get(dim_id, set()))
        if not vals:
            continue
        name_map = dim_names.get(dim_id, {})
        print(f"  {dim_id} ({len(vals)} values):")
        for v in vals[:30]:
            label = name_map.get(v, "")
            print(f"    {v}{f' — {label}' if label else ''}")
        if len(vals) > 30:
            print(f"    ... +{len(vals)-30} more")
        print()

    print(
        "Use only these values when constructing your key.",
        file=sys.stderr,
    )


def cmd_codelist(args):
    df_id = args.dataflow_id
    dim_target = args.dimension_id.upper()

    print(f"Codelist for {dim_target} in {df_id}...", file=sys.stderr)
    root = _get_structure(df_id, refresh=args.refresh)

    target_cl = None
    for dim in root.iter(f"{{{NS['str']}}}Dimension"):
        if (dim.get("id", "")).upper() == dim_target:
            for ref in dim.iter("Ref"):
                if ref.get("class") == "Codelist":
                    target_cl = ref.get("id", "")
            break

    if not target_cl:
        print(f"Dimension '{dim_target}' not found.", file=sys.stderr)
        sys.exit(1)

    codes = _parse_codelists(root).get(target_cl, [])
    if not codes:
        print(f"No codes in {target_cl}.", file=sys.stderr)
        return

    if args.filter:
        t = args.filter.lower()
        codes = [(c, n) for c, n in codes if t in c.lower() or t in n.lower()]
        if not codes:
            print(f"No codes matching '{args.filter}'.", file=sys.stderr)
            return

    print(f"\n{dim_target} — {target_cl} ({len(codes)} values)\n")
    print(f"{'CODE':<20} NAME")
    print("-" * 80)
    for cid, name in codes:
        print(f"{cid:<20} {name[:65]}{'...' if len(name)>65 else ''}")


def cmd_preview(args):
    """Fetch a small sample (5 obs) to test a key before full download.

    Uses firstNObservations=5 as ISTAT recommends for safe exploration.
    Costs 1 API call but prevents wasted bandwidth and rate-limit burns
    from bad queries.
    """
    df_id = args.dataflow_id
    key = args.key or ""

    path = f"data/{df_id}/{key}" if key else f"data/{df_id}"
    path += "?firstNObservations=5"

    print(f"Preview: {df_id} key={key or '(all)'}...", file=sys.stderr)
    result = _fetch_csv(path)

    lines = result.strip().split("\n")
    print(f"\n{len(lines)-1} sample observations:\n")
    # Print header and first rows nicely
    for line in lines[:21]:  # header + up to 20 rows
        print(line)
    if len(lines) > 21:
        print(f"... ({len(lines)-1} total in sample)")

    print(
        f"\nKey works. Use 'data {df_id} {key}' for full download.",
        file=sys.stderr,
    )


def cmd_data(args):
    df_id = args.dataflow_id
    key = args.key or ""

    path = f"data/{df_id}/{key}" if key else f"data/{df_id}"

    params = []
    if args.start:
        params.append(f"startPeriod={args.start}")
    if args.end:
        try:
            adjusted = str(int(args.end) - 1)
            params.append(f"endPeriod={adjusted}")
            print(
                f"Note: endPeriod adjusted to {adjusted} (ISTAT bug: +1 year).",
                file=sys.stderr,
            )
        except ValueError:
            params.append(f"endPeriod={args.end}")
    if args.last:
        params.append(f"lastNObservations={args.last}")

    if params:
        path += "?" + "&".join(params)

    fmt = args.format or "csv"

    if fmt == "csv":
        print(f"Fetching {df_id}...", file=sys.stderr)
        result = _fetch_csv(path)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(result)
            n = result.strip().count("\n")
            print(f"Wrote {n} observations to {args.out}", file=sys.stderr)
        else:
            print(result)

    elif fmt == "json":
        print(f"Fetching {df_id}...", file=sys.stderr)
        raw = _fetch_live(path, accept="application/vnd.sdmx.data+json;version=1.0.0", timeout=120)
        result = raw.decode("utf-8")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(result)
            print(f"Wrote JSON to {args.out}", file=sys.stderr)
        else:
            try:
                print(json.dumps(json.loads(result), indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query ISTAT via SDMX REST API (with local cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. list --search TOPIC     (cached)\n"
            "  2. structure DATAFLOW      (cached)\n"
            "  3. available DATAFLOW      (cached — shows valid codes)\n"
            "  4. codelist DATAFLOW DIM   (cached)\n"
            "  5. preview DATAFLOW KEY    (1 API call — 5 obs test)\n"
            "  6. data DATAFLOW KEY       (1 API call — full download)\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # list
    p = sub.add_parser("list", help="Search dataflow catalog")
    p.add_argument("--search", "-s", help="Filter by keyword")
    p.add_argument("--refresh", action="store_true")

    # structure
    p = sub.add_parser("structure", help="Show dataflow dimensions")
    p.add_argument("dataflow_id")
    p.add_argument("--refresh", action="store_true")

    # available
    p = sub.add_parser(
        "available",
        help="Show which codes actually exist (availableconstraint)",
    )
    p.add_argument("dataflow_id")
    p.add_argument("--refresh", action="store_true")

    # codelist
    p = sub.add_parser("codelist", help="Full codelist for a dimension")
    p.add_argument("dataflow_id")
    p.add_argument("dimension_id")
    p.add_argument("--filter", "-f", help="Filter codes by keyword")
    p.add_argument("--refresh", action="store_true")

    # preview
    p = sub.add_parser("preview", help="Test a key with 5 observations")
    p.add_argument("dataflow_id")
    p.add_argument("key", nargs="?", default="")

    # data
    p = sub.add_parser("data", help="Fetch full data")
    p.add_argument("dataflow_id")
    p.add_argument("key", nargs="?", default="")
    p.add_argument("--start", help="Start period (YYYY or YYYY-MM)")
    p.add_argument("--end", help="End period (YYYY or YYYY-MM)")
    p.add_argument("--last", type=int, help="Last N observations per series")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--out", "-o", help="Output file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "list": cmd_list,
        "structure": cmd_structure,
        "available": cmd_available,
        "codelist": cmd_codelist,
        "preview": cmd_preview,
        "data": cmd_data,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

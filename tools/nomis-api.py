#!/usr/bin/env python3
"""Query NOMIS (ONS) UK labour market and census data via REST API.

All metadata is cached locally after first fetch. Only `data` queries hit
the live API on subsequent runs. Cache lives in .cache/nomis/.

NOMIS rate limit: unregistered users get lower limits. Register at
nomisweb.co.uk for higher rate limits and set NOMIS_API_KEY env var.
This tool enforces 1s between requests as a baseline.

Key datasets:
    NM_189_1    BRES employment by industry (local authority, TTWA, ward)
    NM_30_1     ASHE earnings (residence/workplace, LA)
    NM_141_1    APS labour market (economic activity, LA)
    NM_57_1     Census 2021 (various tables)

the metro region geography:
    E10000000               GM county (metropolitan county)
    E08000000-E08000000     10 GM boroughs (Northgate, Eastgate, Exampleton,
                            Hillgate, Rivergate, Westgate, Midgate,
                            Valegate, Southgate, Lakegate)
    TYPE464                 Local authorities
    TYPE480                 Regions

Commands:
    search      Search the dataset catalog (cached 7 days).
    structure   Show dimensions for a dataset (cached 7 days).
    geography   Show available geographies for a dataset (cached 7 days).
    codelist    Show codes for a specific dimension (cached 7 days).
    preview     Fetch a small sample (5 obs) to verify query.
    data        Fetch observations (always live).

Recommended workflow:
    1. search --term "employment"           # find dataset ID      (cached)
    2. structure NM_189_1                   # see dimensions        (cached)
    3. geography NM_189_1 --search "manch"  # find geo codes        (cached)
    4. codelist NM_189_1 INDUSTRY           # browse a dimension    (cached)
    5. preview NM_189_1 --geo E10000000     # test with 5 obs       (1 API call)
    6. data NM_189_1 --geo E10000000 --out file.csv  # full download

Usage:
    python3 tools/nomis-api.py search [--term TERM]
    python3 tools/nomis-api.py structure DATASET_ID
    python3 tools/nomis-api.py geography DATASET_ID [--search TERM]
    python3 tools/nomis-api.py codelist DATASET_ID DIMENSION [--filter TERM]
    python3 tools/nomis-api.py preview DATASET_ID [--geo GEO] [--time TIME] [options]
    python3 tools/nomis-api.py data DATASET_ID [--geo GEO] [--time TIME] [--out FILE] [options]

All metadata commands accept --refresh to bypass cache.

Examples:
    python3 tools/nomis-api.py search --term "business register"
    python3 tools/nomis-api.py structure NM_189_1
    python3 tools/nomis-api.py geography NM_189_1 --search "exampleton"
    python3 tools/nomis-api.py codelist NM_189_1 INDUSTRY --filter "manufacturing"
    python3 tools/nomis-api.py preview NM_189_1 --geo E10000000 --time 2022
    python3 tools/nomis-api.py data NM_189_1 --geo E10000000 --time 2015-2023 --out bres.csv
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

BASE_URL = "https://www.nomisweb.co.uk/api/v01"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0  # seconds — generous for unregistered users

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "nomis")

METADATA_TTL = 7 * 86400  # 7 days

# the metro region reference codes
GM_COUNTY = "E10000000"
GM_BOROUGHS = {
    "E08000000": "Northgate",
    "E08000000": "Eastgate",
    "E08000000": "Exampleton",
    "E08000000": "Hillgate",
    "E08000000": "Rivergate",
    "E08000000": "Westgate",
    "E08000000": "Midgate",
    "E08000000": "Valegate",
    "E08000000": "Southgate",
    "E08000000": "Lakegate",
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


def _get_api_key():
    return os.environ.get("NOMIS_API_KEY", "")


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _fetch(path, params=None, timeout=120):
    """Fetch from NOMIS API. Returns parsed JSON or raw text."""
    if params is None:
        params = {}

    # Add API key if available
    api_key = _get_api_key()
    if api_key:
        params["uid"] = api_key

    # Build URL
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{path.lstrip('/')}"
    if query:
        url += ("&" if "?" in url else "?") + query

    _rate_limit()

    headers = {"User-Agent": "QLE-Infrastructure/1.0"}
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
            403: "Forbidden — may need API key. Register at nomisweb.co.uk.",
            404: f"Dataset or resource not found. URL: {url}",
            429: "Rate limited. Wait before retrying or register for API key.",
            500: f"NOMIS server error (500). URL: {url}",
        }
        print(
            f"Error: {msgs.get(e.code, f'HTTP {e.code} — {url}')}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(path, params=None, timeout=120):
    """Fetch and parse JSON from NOMIS API."""
    if params is None:
        params = {}
    params["select"] = params.get("select", "")  # ensure we can add select
    data = _fetch(path, params, timeout)
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        # Sometimes NOMIS returns CSV even when we want JSON
        # Try adding .json to path
        return data


def _fetch_cached(key, path, params=None, ttl=METADATA_TTL, refresh=False, timeout=120):
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
# Commands
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Search the NOMIS dataset catalog."""
    print("Searching NOMIS dataset catalog...", file=sys.stderr)

    cache_key = f"catalog_{args.term or 'all'}"
    params = {"search": f"*{args.term}*"} if args.term else {}

    raw = _fetch_cached(
        cache_key,
        "dataset/def.sdmx.json",
        params=params,
        refresh=args.refresh,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse catalog response.", file=sys.stderr)
        sys.exit(1)

    # Navigate SDMX-JSON structure
    datasets = []
    try:
        keyfamilies = data.get("structure", {}).get("keyfamilies", {}).get("keyfamily", [])
        for kf in keyfamilies:
            ds_id = kf.get("id", "")
            name_obj = kf.get("name", {})
            if isinstance(name_obj, dict):
                name = name_obj.get("value", "")
            elif isinstance(name_obj, str):
                name = name_obj
            else:
                name = str(name_obj)

            # Get annotations for description
            desc = ""
            annotations = kf.get("annotations", {}).get("annotation", [])
            if isinstance(annotations, list):
                for ann in annotations:
                    if ann.get("annotationtitle") == "MetadataText0":
                        desc = ann.get("annotationtext", "")
                        break

            datasets.append((ds_id, name, desc))
    except (KeyError, TypeError, AttributeError):
        print("Warning: Unexpected catalog format.", file=sys.stderr)
        # Try flat format
        if isinstance(data, list):
            for item in data:
                datasets.append((
                    item.get("id", ""),
                    item.get("name", ""),
                    item.get("description", ""),
                ))

    if not datasets:
        print("No datasets found.", file=sys.stderr)
        return

    # Filter by term if not already done by API
    if args.term and not params.get("search"):
        t = args.term.lower()
        datasets = [
            (d, n, desc) for d, n, desc in datasets
            if t in d.lower() or t in n.lower() or t in desc.lower()
        ]

    datasets.sort()
    print(f"\n{'ID':<20} NAME")
    print("-" * 100)
    for ds_id, name, desc in datasets:
        display = name[:80] or desc[:80]
        print(f"{ds_id:<20} {display}")

    print(f"\n{len(datasets)} datasets found.", file=sys.stderr)


def cmd_structure(args):
    """Show dimensions for a dataset."""
    ds_id = args.dataset_id
    print(f"Structure of {ds_id}...", file=sys.stderr)

    cache_key = f"structure_{ds_id}"
    raw = _fetch_cached(
        cache_key,
        f"dataset/{ds_id}/def.sdmx.json",
        refresh=args.refresh,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse structure response.", file=sys.stderr)
        sys.exit(1)

    # Parse dimensions from SDMX-JSON
    dims = []
    try:
        keyfamilies = data.get("structure", {}).get("keyfamilies", {}).get("keyfamily", [])
        if keyfamilies:
            kf = keyfamilies[0] if isinstance(keyfamilies, list) else keyfamilies
            components = kf.get("components", {})

            # Dimensions
            dimensions = components.get("dimension", [])
            if isinstance(dimensions, dict):
                dimensions = [dimensions]
            for dim in dimensions:
                dim_id = dim.get("conceptref", "")
                codelist_id = dim.get("codelist", "")
                dims.append((dim_id, codelist_id))

            # Time dimension
            time_dims = components.get("timedimension", [])
            if isinstance(time_dims, dict):
                time_dims = [time_dims]
            for td in time_dims:
                dims.append((td.get("conceptref", "TIME"), "(time)"))
    except (KeyError, TypeError):
        print("Warning: Unexpected structure format.", file=sys.stderr)

    if not dims:
        print("No dimensions found.", file=sys.stderr)
        return

    print(f"\nDataset: {ds_id}")
    print(f"\n{'DIMENSION':<30} CODELIST")
    print("-" * 70)
    for dim_id, cl_id in dims:
        print(f"{dim_id:<30} {cl_id}")

    # Also fetch and show concept descriptions
    print(
        f"\nUse 'codelist {ds_id} DIMENSION' to see values for a dimension.",
        file=sys.stderr,
    )


def cmd_geography(args):
    """Show available geographies for a dataset."""
    ds_id = args.dataset_id
    print(f"Geographies for {ds_id}...", file=sys.stderr)

    cache_key = f"geography_{ds_id}_{args.search or 'all'}"

    # NOMIS geography endpoint
    params = {"select": "id,label"}
    if args.search:
        params["search"] = f"*{args.search}*"

    raw = _fetch_cached(
        cache_key,
        f"dataset/{ds_id}/geography.def.sdmx.json",
        params=params,
        refresh=args.refresh,
        timeout=180,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error parsing geography response.", file=sys.stderr)
        sys.exit(1)

    # Parse geography types and codes
    geos = []
    try:
        keyfamilies = data.get("structure", {}).get("keyfamilies", {}).get("keyfamily", [])
        if keyfamilies:
            kf = keyfamilies[0] if isinstance(keyfamilies, list) else keyfamilies
            components = kf.get("components", {})
            dimensions = components.get("dimension", [])
            if isinstance(dimensions, dict):
                dimensions = [dimensions]
            for dim in dimensions:
                codelist_items = dim.get("code", [])
                if isinstance(codelist_items, dict):
                    codelist_items = [codelist_items]
                for code in codelist_items:
                    code_val = code.get("value", "")
                    desc = code.get("description", "")
                    if isinstance(desc, dict):
                        desc = desc.get("value", "")
                    geos.append((code_val, desc))
    except (KeyError, TypeError):
        # Try alternate format — NOMIS sometimes returns simpler JSON
        pass

    # If SDMX format didn't work, try the simpler /geography endpoint
    if not geos:
        cache_key2 = f"geo_simple_{ds_id}_{args.search or 'all'}"
        params2 = {}
        if args.search:
            params2["search"] = f"*{args.search}*"

        raw2 = _fetch_cached(
            cache_key2,
            f"dataset/{ds_id}/geography.def.sdmx.json",
            params=params2,
            refresh=args.refresh,
        )
        try:
            data2 = json.loads(raw2)
            # Try to extract from various possible structures
            if isinstance(data2, dict):
                for key in ["geographies", "geography", "items"]:
                    if key in data2:
                        items = data2[key]
                        if isinstance(items, list):
                            for item in items:
                                geos.append((
                                    item.get("code", item.get("id", "")),
                                    item.get("name", item.get("label", "")),
                                ))
        except (json.JSONDecodeError, KeyError):
            pass

    if not geos:
        # Fall back: show known GM geography as helpful reference
        print("\nCould not parse geography list from API.", file=sys.stderr)
        print("Known the metro region codes:", file=sys.stderr)
        print(f"  {GM_COUNTY} — the metro region (county)", file=sys.stderr)
        for code, name in sorted(GM_BOROUGHS.items()):
            print(f"  {code} — {name}", file=sys.stderr)
        print("\nGeography types: TYPE464 (local authorities), TYPE480 (regions)", file=sys.stderr)
        return

    print(f"\n{'CODE':<20} GEOGRAPHY")
    print("-" * 80)
    for code, label in geos[:100]:
        print(f"{code:<20} {label[:65]}")

    if len(geos) > 100:
        print(f"\n... +{len(geos) - 100} more. Use --search to narrow.", file=sys.stderr)

    print(f"\n{len(geos)} geographies.", file=sys.stderr)


def cmd_codelist(args):
    """Show codes for a specific dimension."""
    ds_id = args.dataset_id
    dim_id = args.dimension.upper()

    print(f"Codelist for {dim_id} in {ds_id}...", file=sys.stderr)

    cache_key = f"codelist_{ds_id}_{dim_id}"
    raw = _fetch_cached(
        cache_key,
        f"dataset/{ds_id}/{dim_id}.def.sdmx.json",
        refresh=args.refresh,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse codelist response.", file=sys.stderr)
        sys.exit(1)

    codes = []
    try:
        # NOMIS SDMX format for codelists
        structure = data.get("structure", {})
        codelists = structure.get("codelists", {}).get("codelist", [])
        if isinstance(codelists, dict):
            codelists = [codelists]
        for cl in codelists:
            code_items = cl.get("code", [])
            if isinstance(code_items, dict):
                code_items = [code_items]
            for c in code_items:
                val = str(c.get("value", c.get("id", "")))
                desc = c.get("description", c.get("name", ""))
                if isinstance(desc, dict):
                    desc = desc.get("value", "")
                codes.append((val, str(desc)))
    except (KeyError, TypeError):
        pass

    if not codes:
        print(f"No codes found for {dim_id}.", file=sys.stderr)
        return

    # Filter
    if args.filter:
        t = args.filter.lower()
        codes = [(c, d) for c, d in codes if t in c.lower() or t in d.lower()]
        if not codes:
            print(f"No codes matching '{args.filter}'.", file=sys.stderr)
            return

    print(f"\n{dim_id} ({len(codes)} values)\n")
    print(f"{'CODE':<20} DESCRIPTION")
    print("-" * 80)
    for code, desc in codes:
        print(f"{code:<20} {desc[:65]}")


def cmd_preview(args):
    """Fetch a small sample (5 obs) to test a query."""
    ds_id = args.dataset_id
    print(f"Preview: {ds_id}...", file=sys.stderr)

    params = _build_data_params(args)
    params["recordlimit"] = "5"

    raw = _fetch(f"dataset/{ds_id}.data.csv", params=params)

    lines = raw.strip().split("\n")
    print(f"\n{len(lines) - 1} sample observations:\n")
    for line in lines[:21]:
        print(line)
    if len(lines) > 21:
        print(f"... ({len(lines) - 1} total in sample)")

    print(
        f"\nQuery works. Use 'data {ds_id}' with same parameters for full download.",
        file=sys.stderr,
    )


def cmd_data(args):
    """Fetch full data."""
    ds_id = args.dataset_id
    print(f"Fetching {ds_id}...", file=sys.stderr)

    params = _build_data_params(args)

    raw = _fetch(f"dataset/{ds_id}.data.csv", params=params, timeout=300)

    if args.out:
        # Ensure output directory exists
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        n = raw.strip().count("\n")
        print(f"Wrote {n} observations to {args.out}", file=sys.stderr)
    else:
        print(raw)


def _build_data_params(args):
    """Build query parameters from command-line arguments."""
    params = {}

    # Geography
    if hasattr(args, "geo") and args.geo:
        geo_val = args.geo
        # Support comma-separated geography codes
        if "," in geo_val:
            params["geography"] = geo_val
        elif geo_val.startswith("TYPE"):
            params["geography"] = geo_val
        else:
            params["geography"] = geo_val

    # Time
    if hasattr(args, "time") and args.time:
        time_val = args.time
        if "-" in time_val and not time_val.startswith("-"):
            # Range like 2015-2023
            parts = time_val.split("-")
            if len(parts) == 2:
                try:
                    start_yr = int(parts[0])
                    end_yr = int(parts[1])
                    years = ",".join(str(y) for y in range(start_yr, end_yr + 1))
                    params["time"] = years
                except ValueError:
                    params["time"] = time_val
            else:
                params["time"] = time_val
        else:
            params["time"] = time_val

    # Industry / other dimension filters
    if hasattr(args, "industry") and args.industry:
        params["industry"] = args.industry

    if hasattr(args, "employment_status") and args.employment_status:
        params["employment_status"] = args.employment_status

    if hasattr(args, "measure") and args.measure:
        params["measures"] = args.measure

    if hasattr(args, "sex") and args.sex:
        params["sex"] = args.sex

    # Additional raw parameters (key=value pairs)
    if hasattr(args, "param") and args.param:
        for p in args.param:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = v

    return params


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query NOMIS (ONS) UK data via REST API (with local cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended workflow:\n"
            "  1. search --term TOPIC        (cached)\n"
            "  2. structure DATASET           (cached)\n"
            "  3. geography DATASET           (cached)\n"
            "  4. codelist DATASET DIM        (cached)\n"
            "  5. preview DATASET --geo CODE  (1 API call — 5 obs test)\n"
            "  6. data DATASET --geo CODE     (1 API call — full download)\n"
            "\n"
            "the metro region: --geo E10000000 (county) or\n"
            "  --geo E08000000,E08000000,...,E08000000 (boroughs)\n"
            "\n"
            "Key datasets:\n"
            "  NM_189_1   BRES employment by industry\n"
            "  NM_30_1    ASHE earnings\n"
            "  NM_141_1   APS labour market\n"
            "  NM_57_1    Census 2021\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search dataset catalog")
    p.add_argument("--term", "-t", help="Search keyword")
    p.add_argument("--refresh", action="store_true")

    # structure
    p = sub.add_parser("structure", help="Show dataset dimensions")
    p.add_argument("dataset_id")
    p.add_argument("--refresh", action="store_true")

    # geography
    p = sub.add_parser("geography", help="Show available geographies")
    p.add_argument("dataset_id")
    p.add_argument("--search", "-s", help="Filter by keyword")
    p.add_argument("--refresh", action="store_true")

    # codelist
    p = sub.add_parser("codelist", help="Show codes for a dimension")
    p.add_argument("dataset_id")
    p.add_argument("dimension")
    p.add_argument("--filter", "-f", help="Filter codes by keyword")
    p.add_argument("--refresh", action="store_true")

    # preview
    p = sub.add_parser("preview", help="Test a query with 5 observations")
    p.add_argument("dataset_id")
    p.add_argument("--geo", help="Geography code(s)")
    p.add_argument("--time", help="Time period (YYYY or YYYY-YYYY range)")
    p.add_argument("--industry", help="Industry code(s)")
    p.add_argument("--employment_status", help="Employment status code(s)")
    p.add_argument("--measure", help="Measure code(s)")
    p.add_argument("--sex", help="Sex code(s)")
    p.add_argument("--param", "-p", action="append", help="Extra param key=value")

    # data
    p = sub.add_parser("data", help="Fetch full data")
    p.add_argument("dataset_id")
    p.add_argument("--geo", help="Geography code(s)")
    p.add_argument("--time", help="Time period (YYYY or YYYY-YYYY range)")
    p.add_argument("--industry", help="Industry code(s)")
    p.add_argument("--employment_status", help="Employment status code(s)")
    p.add_argument("--measure", help="Measure code(s)")
    p.add_argument("--sex", help="Sex code(s)")
    p.add_argument("--param", "-p", action="append", help="Extra param key=value")
    p.add_argument("--out", "-o", help="Output file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "structure": cmd_structure,
        "geography": cmd_geography,
        "codelist": cmd_codelist,
        "preview": cmd_preview,
        "data": cmd_data,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

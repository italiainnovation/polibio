#!/usr/bin/env python3
"""Query datos.gob.es — Spain's national open data portal — via its CKAN API.

datos.gob.es is a meta-catalog of 90,000+ datasets from all Spanish public
administrations. It is the discovery layer for data that is published by
agencies with no direct API (SEPE unemployment, CNMV filings, AEAT tax
statistics, regional statistics, etc.).

API base: https://datos.gob.es/apidata/catalog/
No authentication required. JSON responses.

Cache lives in .cache/datos-gob-es/.

Two primary use cases:
  1. DISCOVERY: Find datasets by keyword, organisation, or theme.
  2. ACCESS: Get the download URL for a specific dataset's resources,
     then download the actual data file (CSV, JSON, Excel).

Key organisations for Spanish institutional:
  INE         Instituto Nacional de Estadística
  CNMV        Comisión Nacional del Mercado de Valores (securities regulator)
  SEPE        Servicio Público de Empleo Estatal (employment service)
  AEAT        Agencia Estatal de Administración Tributaria (tax agency)
  Registro    Registradores de España
  SS          Seguridad Social
  IGE / IEA   Regional statistics institutes (the region, Andalucía, etc.)

Commands:
    search      Search datasets by keyword (cached 7 days).
    dataset     Get full metadata and download URLs for a dataset (cached 7 days).
    download    Download a resource file from a dataset.
    themes      List available thematic categories (cached 7 days).
    orgs        List publisher organisations matching a search term (cached 7 days).

Recommended workflow:
    1. search "afiliados municipios"              # find dataset ID
    2. dataset DATASET_ID                         # get download URLs
    3. download RESOURCE_URL --out data.csv       # fetch the data file

Usage:
    python3 tools/datos-gob-es-api.py search QUERY [--theme THEME] [--org ORG] [--limit N]
    python3 tools/datos-gob-es-api.py dataset DATASET_ID
    python3 tools/datos-gob-es-api.py download URL --out FILE
    python3 tools/datos-gob-es-api.py themes [--refresh]
    python3 tools/datos-gob-es-api.py orgs SEARCH_TERM [--refresh]

Examples:
    python3 tools/datos-gob-es-api.py search "paro registrado municipios"
    python3 tools/datos-gob-es-api.py search "CNMV hechos relevantes"
    python3 tools/datos-gob-es-api.py search "empresas directorio DIRCE" --org ine
    python3 tools/datos-gob-es-api.py search "contratos trabajo municipio" --limit 20
    python3 tools/datos-gob-es-api.py dataset l01280796-contratos-registrados
    python3 tools/datos-gob-es-api.py download https://datos.sepe.es/... --out sepe.csv
    python3 tools/datos-gob-es-api.py themes
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

BASE_URL = "https://datos.gob.es/apidata/catalog"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.5

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR     = os.path.join(_PROJECT_ROOT, ".cache", "datos-gob-es")

METADATA_TTL = 7 * 86400


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


def _fetch(url: str, accept: str = "application/json", timeout: int = 60) -> str:
    _rate_limit()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "QLE-Infrastructure/1.0", "Accept": accept},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        print(f"Error: HTTP {e.code} — {url}\n{body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_json(url: str, ttl: float = METADATA_TTL,
                refresh: bool = False, cache_key: str = None) -> dict:
    if cache_key:
        if not refresh:
            cached = _cache_read(cache_key, ttl)
            if cached:
                print("  (from cache)", file=sys.stderr)
                try:
                    return json.loads(cached)
                except json.JSONDecodeError:
                    pass
        raw = _fetch(url)
        _cache_write(cache_key, raw)
    else:
        raw = _fetch(url)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse JSON — {e}", file=sys.stderr)
        print(f"Raw (first 500): {raw[:500]}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(obj, lang: str = "es") -> str:
    """Extract Spanish text from multilingual objects."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get(lang, obj.get("en", next(iter(obj.values()), "")))
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get("@language") == lang:
                return item.get("@value", "")
        if obj:
            v = obj[0]
            if isinstance(v, dict):
                return v.get("@value", str(v))
            return str(v)
    return str(obj)


def _dataset_id(ds: dict) -> str:
    """Extract dataset ID from a dataset object."""
    return (ds.get("id") or ds.get("identifier", "") or
            ds.get("@id", "").split("/")[-1])


def _dataset_title(ds: dict) -> str:
    return _extract_text(ds.get("title", ds.get("dct:title", "")))


def _dataset_org(ds: dict) -> str:
    pub = ds.get("publisher", ds.get("dct:publisher", {}))
    if isinstance(pub, dict):
        return _extract_text(pub.get("name", pub.get("foaf:name", "")))
    return str(pub)


def _dataset_resources(ds: dict) -> list:
    """Extract download URLs from a dataset."""
    dist = ds.get("distribution", ds.get("dcat:distribution", []))
    if isinstance(dist, dict):
        dist = [dist]
    resources = []
    for d in dist:
        url  = d.get("accessURL", d.get("downloadURL", d.get("dcat:accessURL", "")))
        fmt  = _extract_text(d.get("format", d.get("dct:format", "")))
        desc = _extract_text(d.get("description", d.get("dct:description", "")))
        if isinstance(url, dict):
            url = url.get("@id", "")
        if url:
            resources.append({"url": str(url), "format": fmt, "description": desc})
    return resources


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Search datasets by keyword."""
    query = args.query
    params = {"q": query, "_pageSize": str(args.limit), "_page": "0"}
    if args.theme:
        params["theme"] = args.theme
    if args.org:
        params["publisher"] = args.org

    url       = f"{BASE_URL}/dataset?" + urllib.parse.urlencode(params)
    cache_key = f"search_{query}_{args.theme or ''}_{args.org or ''}_{args.limit}"

    print(f"Searching datos.gob.es for '{query}'...", file=sys.stderr)
    data = _fetch_json(url, ttl=METADATA_TTL, refresh=args.refresh, cache_key=cache_key)

    # Parse results — datos.gob.es returns JSON-LD
    items = (data.get("result", {}).get("items", []) or
             data.get("@graph", []) or
             data.get("results", []) or
             (data if isinstance(data, list) else []))

    if not items:
        print(f"No results for '{query}'.", file=sys.stderr)
        # Show raw structure hint
        print(f"Response keys: {list(data.keys())[:10]}", file=sys.stderr)
        return

    print(f"\n{'DATASET_ID':<45} {'ORG':<25} TITLE")
    print("-" * 120)
    for ds in items[:args.limit]:
        ds_id  = _dataset_id(ds)
        title  = _dataset_title(ds)[:55]
        org    = _dataset_org(ds)[:22]
        print(f"{ds_id:<45} {org:<25} {title}")
    print(f"\n{len(items)} results.", file=sys.stderr)
    print("Use 'dataset DATASET_ID' to see download URLs.", file=sys.stderr)


def cmd_dataset(args):
    """Get full metadata and download URLs for a dataset."""
    ds_id = args.dataset_id
    url   = f"{BASE_URL}/dataset/{ds_id}"

    print(f"Fetching dataset {ds_id}...", file=sys.stderr)
    data = _fetch_json(url, ttl=METADATA_TTL, refresh=args.refresh,
                       cache_key=f"dataset_{ds_id}")

    # Handle JSON-LD wrapper
    if "@graph" in data:
        graph = data["@graph"]
        ds    = next((g for g in graph if "title" in g or "dct:title" in g), graph[0] if graph else data)
    else:
        ds = data

    title  = _dataset_title(ds)
    org    = _dataset_org(ds)
    desc   = _extract_text(ds.get("description", ds.get("dct:description", "")))
    issued = ds.get("issued", ds.get("dct:issued", ""))
    mods   = ds.get("modified", ds.get("dct:modified", ""))

    print(f"\nDataset: {ds_id}")
    print(f"Title:   {title}")
    print(f"Org:     {org}")
    if desc:
        print(f"Desc:    {desc[:200]}")
    if issued:
        print(f"Issued:  {issued}")
    if mods:
        print(f"Updated: {mods}")

    resources = _dataset_resources(ds)
    if resources:
        print(f"\n{len(resources)} distribution(s):\n")
        for i, r in enumerate(resources, 1):
            print(f"  [{i}] Format: {r['format']}")
            print(f"      URL:    {r['url']}")
            if r["description"]:
                print(f"      Desc:   {r['description'][:80]}")
        print(f"\nUse 'download URL --out FILE' to fetch the data.", file=sys.stderr)
    else:
        print("\nNo download URLs found in metadata.", file=sys.stderr)
        print("Try the dataset page directly on datos.gob.es", file=sys.stderr)


def cmd_download(args):
    """Download a resource file from a URL."""
    url = args.url
    print(f"Downloading {url}...", file=sys.stderr)

    # Detect format from URL
    fmt = "unknown"
    for ext in ["csv", "json", "xlsx", "xls", "xml", "zip"]:
        if f".{ext}" in url.lower():
            fmt = ext
            break

    raw = _fetch(url, accept="*/*", timeout=300)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        n = raw.strip().count("\n")
        print(f"Wrote ~{n} lines ({fmt}) to {args.out}", file=sys.stderr)
    else:
        # Print first 50 lines
        lines = raw.split("\n")
        for line in lines[:50]:
            print(line)
        if len(lines) > 50:
            print(f"\n... ({len(lines)} lines total). Use --out to save full file.")


def cmd_themes(args):
    """List available thematic categories."""
    url = f"{BASE_URL}/theme"
    print("Fetching datos.gob.es themes...", file=sys.stderr)
    data = _fetch_json(url, ttl=METADATA_TTL, refresh=args.refresh,
                       cache_key="themes_all")

    items = (data.get("result", {}).get("items", []) or
             data.get("@graph", []) or
             (data if isinstance(data, list) else []))

    print(f"\n{'THEME_ID':<30} LABEL")
    print("-" * 80)
    for item in items:
        tid   = item.get("id", item.get("@id", "")).split("/")[-1]
        label = _extract_text(item.get("prefLabel", item.get("skos:prefLabel", tid)))
        print(f"{tid:<30} {label}")
    print(f"\n{len(items)} themes.", file=sys.stderr)


def cmd_orgs(args):
    """List publisher organisations matching a search term."""
    params = {"q": args.search, "_pageSize": "30"}
    url    = f"{BASE_URL}/publisher?" + urllib.parse.urlencode(params)
    cache_key = f"orgs_{args.search}"

    print(f"Searching organisations for '{args.search}'...", file=sys.stderr)
    data = _fetch_json(url, ttl=METADATA_TTL, refresh=args.refresh, cache_key=cache_key)

    items = (data.get("result", {}).get("items", []) or
             data.get("@graph", []) or
             (data if isinstance(data, list) else []))

    print(f"\n{'ORG_ID':<40} NAME")
    print("-" * 90)
    for item in items:
        oid  = item.get("id", item.get("@id", "")).split("/")[-1]
        name = _extract_text(item.get("name", item.get("foaf:name", oid)))
        print(f"{oid:<40} {name[:50]}")
    print(f"\n{len(items)} organisations.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query datos.gob.es Spain open data portal (90,000+ datasets) via CKAN API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key agencies for Spanish governance research:\n"
            "  INE:       firm demographics (DIRCE), labour, population\n"
            "  CNMV:      corporate filings, hechos relevantes (SampleCorp)\n"
            "  SEPE:      registered unemployment and contracts by municipality\n"
            "  SS:        social security affiliations (also via seguridad-social-api.py)\n"
            "  AEAT:      tax statistics by sector and province\n"
            "\n"
            "Useful search queries:\n"
            "  'paro registrado municipio'       SEPE unemployment by municipality\n"
            "  'contratos trabajo municipio'     SEPE contracts by municipality\n"
            "  'CNMV hechos relevantes'          significant events from listed firms\n"
            "  'empresas directorio central'     INE DIRCE firm demographics\n"
            "  'renta declarantes municipios'    AEAT income tax by municipality\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search
    p = sub.add_parser("search", help="Search datasets by keyword")
    p.add_argument("query", help="Search keywords")
    p.add_argument("--theme", help="Filter by theme ID")
    p.add_argument("--org",   help="Filter by publisher/organisation ID")
    p.add_argument("--limit", "-n", type=int, default=20, help="Max results (default 20)")
    p.add_argument("--refresh", action="store_true")

    # dataset
    p = sub.add_parser("dataset", help="Get metadata and download URLs for a dataset")
    p.add_argument("dataset_id", help="Dataset ID (from search results)")
    p.add_argument("--refresh", action="store_true")

    # download
    p = sub.add_parser("download", help="Download a resource file from a URL")
    p.add_argument("url", help="Direct download URL (from 'dataset' output)")
    p.add_argument("--out", "-o", required=True, help="Output file path")

    # themes
    p = sub.add_parser("themes", help="List available thematic categories")
    p.add_argument("--refresh", action="store_true")

    # orgs
    p = sub.add_parser("orgs", help="List publisher organisations matching a search")
    p.add_argument("search", help="Organisation name search term")
    p.add_argument("--refresh", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search":   cmd_search,
        "dataset":  cmd_dataset,
        "download": cmd_download,
        "themes":   cmd_themes,
        "orgs":     cmd_orgs,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

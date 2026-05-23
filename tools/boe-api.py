#!/usr/bin/env python3
"""Query the BOE (Boletín Oficial del Estado) and BORME (Boletín Oficial del
Registro Mercantil) via the Spanish government's open data API.

BOE = Spain's official state gazette: legislation, royal decrees, orders,
      consolidated law texts, court judgments, government notices.
BORME = Commercial registry gazette: company registrations, dissolutions,
        director changes, capital modifications, mergers — published daily.

API base: https://boe.es/datosabiertos/api/
No authentication required. XML and JSON responses.

Cache lives in .cache/boe/. Metadata (codelists) cached 7 days.
Legislation text cached 30 days (it doesn't change once published).
BORME daily summaries cached 7 days.

Key use cases:
  - Search legislation: "Real Decreto 4/2014" (ibérico quality standard)
  - Search BORME for company events in Examplio (SampleCorp subsidiaries, etc.)
  - Retrieve full text of a specific norm for legal instrument analysis

Commands:
    search-law   Search consolidated legislation (cached results).
    law          Fetch a specific law by BOE ID or norm ID.
    borme-day    Fetch BORME entries for a specific date.
    boe-day      Fetch BOE summary for a specific date.
    subjects     List available subject taxonomy (cached).

Recommended workflow:
    1. search-law "Real Decreto iberico"       # find norm ID
    2. law BOE-A-2014-12418                    # fetch full text
    3. borme-day 2024-01-15 --search "Examplio" # company events in Examplio

Usage:
    python3 tools/boe-api.py search-law QUERY [--from DATE] [--to DATE] [--limit N]
    python3 tools/boe-api.py law NORM_ID [--section text|metadata|analysis]
    python3 tools/boe-api.py borme-day DATE [--search TERM]
    python3 tools/boe-api.py boe-day DATE [--search TERM]
    python3 tools/boe-api.py subjects

Date format: YYYYMMDD or YYYY-MM-DD

Examples:
    python3 tools/boe-api.py search-law "Real Decreto iberico jamon"
    python3 tools/boe-api.py search-law "SampleCorp" --from 20140101 --to 20241231
    python3 tools/boe-api.py law BOE-A-2014-12418
    python3 tools/boe-api.py borme-day 2024-03-15 --search "Examplio"
    python3 tools/boe-api.py borme-day 2022-04-01 --search "SampleCorp"
    python3 tools/boe-api.py boe-day 2014-01-10 --search "iberico"
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://boe.es/datosabiertos/api"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.5

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR     = os.path.join(_PROJECT_ROOT, ".cache", "boe")

METADATA_TTL = 7  * 86400
LAW_TTL      = 30 * 86400  # legislation text doesn't change
BORME_TTL    = 7  * 86400


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(key: str, ext: str = "xml") -> str:
    safe     = hashlib.sha256(key.encode()).hexdigest()[:16]
    readable = key.replace("/", "_").replace("?", "_").replace("&", "_")[:60]
    return os.path.join(CACHE_DIR, f"{readable}_{safe}.{ext}")


def _cache_read(key: str, ttl: float, ext: str = "xml"):
    path = _cache_path(key, ext)
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) > ttl:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_write(key: str, data: str, ext: str = "xml"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key, ext), "w", encoding="utf-8") as f:
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


def _fetch(path: str, params: dict = None, accept: str = "application/xml",
           timeout: int = 60) -> str:
    params = params or {}
    query  = urllib.parse.urlencode(params)
    url    = f"{BASE_URL}/{path.lstrip('/')}"
    if query:
        url += ("&" if "?" in url else "?") + query

    _rate_limit()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "QLE-Infrastructure/1.0", "Accept": accept},
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
        print(f"Error: Cannot connect to BOE API — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key: str, path: str, params: dict = None,
                  ttl: float = METADATA_TTL, refresh: bool = False,
                  accept: str = "application/xml", ext: str = "xml") -> str:
    if not refresh:
        cached = _cache_read(key, ttl, ext)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached
    data = _fetch(path, params, accept=accept)
    _cache_write(key, data, ext)
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_date(d: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD."""
    return d.replace("-", "")


def _xml_text(element, tag: str, default: str = "") -> str:
    """Get text content of a child element."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _strip_tags(text: str) -> str:
    """Remove XML/HTML tags from text."""
    return re.sub(r"<[^>]+>", " ", text).strip()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_search_law(args):
    """Search consolidated legislation."""
    query = args.query
    print(f"Searching BOE legislation for '{query}'...", file=sys.stderr)

    params = {"query": query}
    if args.from_date:
        params["from"] = _normalise_date(args.from_date)
    if args.to_date:
        params["to"] = _normalise_date(args.to_date)
    params["limit"] = str(args.limit)

    cache_key = f"law_search_{query}_{params.get('from', '')}_{params.get('to', '')}_{args.limit}"
    raw = _fetch_cached(cache_key, "legislacion-consolidada",
                        params=params, ttl=METADATA_TTL, ext="xml",
                        accept="application/xml", refresh=args.refresh)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Try as JSON
        try:
            data = json.loads(raw)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
            return
        except json.JSONDecodeError:
            print(f"Could not parse response. Raw (first 1000 chars):\n{raw[:1000]}")
            return

    # Extract results from XML
    results = []
    # BOE uses various XML structures — try common patterns
    for item in root.iter():
        norm_id = item.get("id", "")
        if not norm_id:
            continue
        title    = _xml_text(item, "titulo") or _xml_text(item, "title")
        fecha    = _xml_text(item, "fecha") or item.get("fecha", "")
        rango    = _xml_text(item, "rango") or ""
        results.append((norm_id, fecha, rango, title))

    if not results:
        # Fallback: print raw XML excerpt
        print(f"\nRaw response (first 3000 chars):\n{raw[:3000]}")
        return

    print(f"\n{'NORM_ID':<25} {'DATE':<12} {'TYPE':<20} TITLE")
    print("-" * 110)
    for norm_id, fecha, rango, title in results[:args.limit]:
        print(f"{norm_id:<25} {fecha:<12} {rango[:18]:<20} {title[:55]}")
    print(f"\n{len(results)} results.", file=sys.stderr)
    print("Use 'law NORM_ID' to fetch full text.", file=sys.stderr)


def cmd_law(args):
    """Fetch a specific law by norm ID."""
    norm_id = args.norm_id
    section = args.section

    if section == "text":
        endpoint = f"legislacion-consolidada/id/{norm_id}/texto"
    elif section == "analysis":
        endpoint = f"legislacion-consolidada/id/{norm_id}/analisis"
    else:
        endpoint = f"legislacion-consolidada/id/{norm_id}/metadatos"

    print(f"Fetching law {norm_id} ({section})...", file=sys.stderr)

    cache_key = f"law_{norm_id}_{section}"
    raw = _fetch_cached(cache_key, endpoint, ttl=LAW_TTL, ext="xml",
                        accept="application/xml", refresh=args.refresh)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"Saved to {args.out}", file=sys.stderr)
    else:
        # Print readable version
        if section == "text":
            clean = _strip_tags(raw)
            print(clean[:8000])
            if len(clean) > 8000:
                print(f"\n... ({len(clean)} chars total). Use --out to save full text.")
        else:
            print(raw[:4000])


def cmd_borme_day(args):
    """Fetch BORME entries for a specific date."""
    date = _normalise_date(args.date)
    print(f"Fetching BORME for {date}...", file=sys.stderr)

    cache_key = f"borme_{date}"
    raw = _fetch_cached(cache_key, f"borme/sumario/{date}",
                        ttl=BORME_TTL, ext="xml",
                        accept="application/xml", refresh=args.refresh)

    # Parse XML
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw[:3000])
        return

    entries = []
    for item in root.iter():
        # Look for company entries — tag names vary but contain company names
        tag = item.tag.lower()
        if any(t in tag for t in ["acto", "item", "entrada", "registro"]):
            texto = item.text or ""
            empresa = item.get("empresa", item.get("nombre", ""))
            municipio = item.get("municipio", item.get("provincia", ""))
            if texto or empresa:
                entries.append({
                    "tag":       item.tag,
                    "empresa":   empresa,
                    "municipio": municipio,
                    "texto":     (texto or "")[:200],
                })

    if not entries:
        # Show raw for inspection
        clean = _strip_tags(raw)
        if args.search:
            lines = [l for l in clean.split("\n") if args.search.lower() in l.lower()]
            print(f"\n{len(lines)} lines matching '{args.search}':\n")
            for line in lines[:50]:
                print(line.strip())
        else:
            print(clean[:4000])
        return

    if args.search:
        q = args.search.lower()
        entries = [e for e in entries if q in e["empresa"].lower()
                   or q in e["municipio"].lower() or q in e["texto"].lower()]

    print(f"\nBORME {date} — {len(entries)} entries{' matching ' + repr(args.search) if args.search else ''}:\n")
    for e in entries[:50]:
        print(f"  [{e['tag']}] {e['empresa']} ({e['municipio']})")
        if e["texto"]:
            print(f"    {e['texto'][:120]}")
    if len(entries) > 50:
        print(f"  ... +{len(entries) - 50} more")


def cmd_boe_day(args):
    """Fetch BOE summary for a specific date."""
    date = _normalise_date(args.date)
    print(f"Fetching BOE for {date}...", file=sys.stderr)

    cache_key = f"boe_{date}"
    raw = _fetch_cached(cache_key, f"boe/sumario/{date}",
                        ttl=BORME_TTL, ext="xml",
                        accept="application/xml", refresh=args.refresh)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw[:3000])
        return

    items = []
    for item in root.iter():
        tag = item.tag.lower()
        if any(t in tag for t in ["item", "entrada", "disposicion", "anuncio"]):
            item_id = item.get("id", "")
            title   = _xml_text(item, "titulo") or item.get("titulo", "")
            dept    = _xml_text(item, "departamento") or ""
            if title or item_id:
                items.append((item_id, dept, title))

    if not items:
        clean = _strip_tags(raw)
        if args.search:
            lines = [l for l in clean.split("\n") if args.search.lower() in l.lower()]
            print(f"\n{len(lines)} lines matching '{args.search}':\n")
            for line in lines[:30]:
                print(line.strip())
        else:
            print(clean[:5000])
        return

    if args.search:
        q = args.search.lower()
        items = [(i, d, t) for i, d, t in items if q in t.lower() or q in d.lower() or q in i.lower()]

    print(f"\nBOE {date} — {len(items)} items{' matching ' + repr(args.search) if args.search else ''}:\n")
    print(f"{'ID':<25} {'DEPT':<30} TITLE")
    print("-" * 100)
    for item_id, dept, title in items[:50]:
        print(f"{item_id:<25} {dept[:28]:<30} {title[:50]}")
    if len(items) > 50:
        print(f"... +{len(items) - 50} more")
    print("\nUse 'law NORM_ID' to fetch full text.", file=sys.stderr)


def cmd_subjects(args):
    """List available subject taxonomy."""
    print("Fetching BOE subject taxonomy...", file=sys.stderr)
    cache_key = "subjects_all"
    raw = _fetch_cached(cache_key, "datos-auxiliares/materias",
                        ttl=METADATA_TTL, ext="xml",
                        accept="application/xml", refresh=args.refresh)
    try:
        root = ET.fromstring(raw)
        for item in root.iter():
            code = item.get("id", item.get("codigo", ""))
            name = item.text or item.get("nombre", "")
            if code and name:
                print(f"  {code:<10} {name}")
    except ET.ParseError:
        print(raw[:3000])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query BOE/BORME (Spain official gazette + commercial registry) open data API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key use cases:\n"
            "  Legislation: search-law 'Real Decreto 4/2014'   → find ibérico norm\n"
            "  Legislation: search-law 'SampleCorp'               → all BOE mentions\n"
            "  BORME:       borme-day 2022-04-01 --search SampleCorp  → company events\n"
            "  BORME:       borme-day DATE --search Examplio     → local firm events\n"
            "  Full text:   law BOE-A-2014-XXXXX --section text → complete legal text\n"
            "\n"
            "Real Decreto 4/2014 (ibérico quality standard):\n"
            "  BOE date: 2014-01-10 — search-law 'Real Decreto 4 2014 iberico'\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # search-law
    p = sub.add_parser("search-law", help="Search consolidated legislation")
    p.add_argument("query", help="Search query")
    p.add_argument("--from", dest="from_date", help="From date YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--to",   dest="to_date",   help="To date YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--limit", "-n", type=int, default=20, help="Max results (default 20)")
    p.add_argument("--refresh", action="store_true")

    # law
    p = sub.add_parser("law", help="Fetch a specific law by norm ID")
    p.add_argument("norm_id", help="BOE norm ID (e.g. BOE-A-2014-12418)")
    p.add_argument("--section", choices=["metadata", "text", "analysis"],
                   default="metadata", help="Section to fetch (default: metadata)")
    p.add_argument("--out", "-o", help="Output file path")
    p.add_argument("--refresh", action="store_true")

    # borme-day
    p = sub.add_parser("borme-day", help="Fetch BORME commercial registry entries for a date")
    p.add_argument("date", help="Date in YYYYMMDD or YYYY-MM-DD format")
    p.add_argument("--search", "-s", help="Filter entries by keyword (company name, location)")
    p.add_argument("--refresh", action="store_true")

    # boe-day
    p = sub.add_parser("boe-day", help="Fetch BOE gazette summary for a date")
    p.add_argument("date", help="Date in YYYYMMDD or YYYY-MM-DD format")
    p.add_argument("--search", "-s", help="Filter items by keyword")
    p.add_argument("--refresh", action="store_true")

    # subjects
    p = sub.add_parser("subjects", help="List BOE subject taxonomy")
    p.add_argument("--refresh", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search-law": cmd_search_law,
        "law":        cmd_law,
        "borme-day":  cmd_borme_day,
        "boe-day":    cmd_boe_day,
        "subjects":   cmd_subjects,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

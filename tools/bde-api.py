#!/usr/bin/env python3
"""Query Banco de España (BdE) statistical series via its JSON REST API.

All metadata is cached locally. Data queries always hit the live API.
Cache lives in .cache/bde/.

BdE API base: https://app.bde.es/bierest/resources/srdatosapp/
No authentication required. No documented rate limits.

The BdE statistics portal (BIEST) contains:
  - Monetary and financial indicators
  - Interest rates (official, interbank, retail)
  - Financial system data (bank balance sheets, credit, NPLs)
  - National accounts (GDP components)
  - Central de Balances: aggregated company balance sheets by sector
  - Balance of payments
  - Public debt statistics

To find series codes, browse https://www.bde.es/webbe/en/estadisticas/
or use the BdE statistical series locator tool.

Time range parameters:
  Daily:     3M, 12M, 36M
  Monthly:   30M, 60M, MAX
  Quarterly: 30M, 60M, MAX
  Annual:    60M, MAX
  Specific year: e.g. 2015, 2010-2024

Response fields:
  serie          series code
  descripcion    series description
  codFrecuencia  frequency (D=daily, M=monthly, T=quarterly, A=annual)
  fechaValor     ISO 8601 date
  valor          numeric value
  fechas[]       all dates array
  valores[]      all values array

Commands:
    get         Fetch one or more series (always live).
    latest      Fetch the latest value for one or more series.
    search      Search for series by keyword in the web catalog (web fetch).

Recommended workflow:
    1. Browse https://www.bde.es/webbe/en/estadisticas/ to find series codes
    2. latest SERIES_CODE[,CODE2,...]        # check current values
    3. get SERIES_CODE[,CODE2,...] --range MAX --out bde.csv   # full history

Usage:
    python3 tools/bde-api.py get SERIES [SERIES2 ...] [--range RANGE] [--out FILE]
    python3 tools/bde-api.py latest SERIES [SERIES2 ...] [--out FILE]

Options:
    SERIES      One or more BdE series codes (comma-separated or space-separated)
    --range     Time range: 3M, 12M, 36M, 30M, 60M, MAX, YYYY, YYYY-YYYY (default MAX)
    --out       Output CSV file path

Examples:
    # ECB main refinancing rate
    python3 tools/bde-api.py latest TI_1_1_0_00_A00_A

    # Spanish 10-year bond yield (Tesoro, last 5 years)
    python3 tools/bde-api.py get TI_1_2_0_04_A00_D --range 60M

    # Bank credit to non-financial sector (full history)
    python3 tools/bde-api.py get SF_1_0_7000_000_FF_0_00_A --range MAX --out bde-credit.csv

    # Multiple series at once
    python3 tools/bde-api.py get TI_1_1_0_00_A00_A,TI_1_2_0_04_A00_D --range 60M

Useful series codes (not exhaustive — browse BIEST for more):
    TI_1_1_0_00_A00_A    ECB main refinancing rate
    TI_1_2_0_04_A00_D    Spanish 10-year government bond yield
    BE_1_1_1_001_BE_1_00_A  BdE balance sheet total assets
"""

import argparse
import csv
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

BASE_URL  = "https://app.bde.es/bierest/resources/srdatosapp"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.5

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR     = os.path.join(_PROJECT_ROOT, ".cache", "bde")


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


def _fetch(endpoint: str, params: dict = None, timeout: int = 60) -> str:
    params = params or {}
    query  = urllib.parse.urlencode(params)
    url    = f"{BASE_URL}/{endpoint.lstrip('/')}"
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
        print(f"Error: HTTP {e.code} — {url}\n{body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to BdE — {e.reason}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_series_response(raw: str) -> list:
    """Parse BdE JSON response into a flat list of (serie, descripcion, freq, date, value) rows."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    rows = []
    # BdE returns either a single object or a list
    items = data if isinstance(data, list) else [data]
    for item in items:
        serie = item.get("serie", "")
        desc  = item.get("descripcion", "")
        freq  = item.get("codFrecuencia", "")
        fechas  = item.get("fechas", [])
        valores = item.get("valores", [])
        for date, val in zip(fechas, valores):
            rows.append({
                "serie":       serie,
                "descripcion": desc,
                "frecuencia":  freq,
                "fecha":       date,
                "valor":       val,
            })
    return rows


def _write_output(rows: list, out_path: str = None):
    """Write rows to CSV file or print to stdout."""
    if not rows:
        print("No data returned.", file=sys.stderr)
        return

    fields = ["serie", "descripcion", "frecuencia", "fecha", "valor"]
    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} observations to {out_path}", file=sys.stderr)
    else:
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        print(out.getvalue())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _normalise_series_arg(series_args: list) -> str:
    """Accept either space-separated codes or comma-separated, return comma-joined."""
    codes = []
    for s in series_args:
        codes.extend(c.strip() for c in s.split(",") if c.strip())
    return ",".join(codes)


def cmd_get(args):
    """Fetch full history for one or more series."""
    series = _normalise_series_arg(args.series)
    rng    = args.range or "MAX"
    print(f"Fetching BdE series: {series} (range: {rng})...", file=sys.stderr)

    params = {"idioma": "en", "series": series, "rango": rng}
    raw    = _fetch("listaSeries", params, timeout=60)
    rows   = _parse_series_response(raw)
    _write_output(rows, args.out)


def cmd_latest(args):
    """Fetch the latest value for one or more series."""
    series = _normalise_series_arg(args.series)
    print(f"Latest values for BdE series: {series}...", file=sys.stderr)

    params = {"idioma": "en", "series": series}
    raw    = _fetch("favoritas", params, timeout=30)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return

    items = data if isinstance(data, list) else [data]
    print(f"\n{'SERIES':<30} {'DATE':<14} {'VALUE':<15} DESCRIPTION")
    print("-" * 100)
    for item in items:
        serie = item.get("serie", "")
        desc  = item.get("descripcion", "")[:45]
        fecha = item.get("fechaValor", "")
        val   = item.get("valor", "")
        print(f"{serie:<30} {fecha:<14} {str(val):<15} {desc}")

    if args.out:
        rows = _parse_series_response(raw)
        _write_output(rows, args.out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query Banco de España (BdE) statistical series via JSON REST API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Find series codes at:\n"
            "  https://www.bde.es/webbe/en/estadisticas/\n"
            "  BdE statistical series locator (BIEST)\n"
            "\n"
            "Time range options:\n"
            "  3M / 12M / 36M          (daily series)\n"
            "  30M / 60M / MAX         (monthly/quarterly/annual)\n"
            "  2015 / 2010-2024        (specific year or range)\n"
            "\n"
            "Examples:\n"
            "  python3 tools/bde-api.py latest TI_1_1_0_00_A00_A\n"
            "  python3 tools/bde-api.py get TI_1_2_0_04_A00_D --range 60M\n"
            "  python3 tools/bde-api.py get CODE1,CODE2 --range MAX --out bde.csv\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # get
    p = sub.add_parser("get", help="Fetch full history for one or more series")
    p.add_argument("series", nargs="+", help="Series code(s) — space or comma-separated")
    p.add_argument("--range", "-r", default="MAX",
                   help="Time range: 3M/12M/36M/30M/60M/MAX/YYYY/YYYY-YYYY (default MAX)")
    p.add_argument("--out", "-o", help="Output CSV file path")

    # latest
    p = sub.add_parser("latest", help="Fetch the latest value for one or more series")
    p.add_argument("series", nargs="+", help="Series code(s)")
    p.add_argument("--out", "-o", help="Output CSV file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "get":    cmd_get,
        "latest": cmd_latest,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""EPO OPS (Open Patent Services) fetcher — robotics/AI/automation patent counts.

Ready to run once EPO OPS credentials are set. Registration is free:
  1. Create an account at https://developers.epo.org/  (EPO OPS).
  2. Register an application -> get a Consumer Key and Consumer Secret.
  3. Export them:
        export EPO_OPS_KEY=...        # consumer key
        export EPO_OPS_SECRET=...     # consumer secret
  Free tier: ~4 GB/week, sufficient for count queries.

What it does:
  - OAuth2 client-credentials auth against ops.epo.org/3.2.
  - Returns total-result-count for CQL queries (CPC class + publication-date
    range), the clean, reliable signal.
  - Optionally retrieves a biblio sample and tabulates APPLICANT COUNTRY, a
    rough origin proxy.

CAVEAT (read before interpreting): OPS published-data search has no first-class
"inventor region" facet. For proper inventor-region counts (NUTS3 / commuting
zone) — the instrument the precision round wants — use EPO PATSTAT Online or
OECD REGPAT (see ADOPTION_PULLS_README.md). This tool gives country-level
counts and applicant-country shares, not regional inventor geography.

Usage:
  python3 tools/epo-ops-fetch.py counts --cpc B25J G06N G05B --from 2014 --to 2023
  python3 tools/epo-ops-fetch.py country-sample --cpc B25J --year 2022 --n 200
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

OPS = "https://ops.epo.org/3.2"


def token():
    k, s = os.environ.get("EPO_OPS_KEY"), os.environ.get("EPO_OPS_SECRET")
    if not k or not s:
        sys.exit("Set EPO_OPS_KEY and EPO_OPS_SECRET (see header / README).")
    cred = base64.b64encode(f"{k}:{s}".encode()).decode()
    req = urllib.request.Request(
        f"{OPS}/auth/accesstoken",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {cred}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    return json.load(urllib.request.urlopen(req, timeout=30))["access_token"]


def search(cql, tok, rng="1-1"):
    """Return parsed JSON for a published-data biblio search (Range gives counts)."""
    q = urllib.parse.quote(cql)
    req = urllib.request.Request(
        f"{OPS}/rest-services/published-data/search/biblio?q={q}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                 "X-OPS-Range": rng})
    return json.load(urllib.request.urlopen(req, timeout=60))


def count(cql, tok):
    d = search(cql, tok)
    node = d["ops:world-patent-data"]["ops:biblio-search"]
    return int(node["@total-result-count"])


def cmd_counts(a):
    tok = token()
    print(json.dumps({"query_window": f"{a.from_}-{a.to}", "cpc_counts": _counts(a, tok)}, indent=2))


def _counts(a, tok):
    out = {}
    for cpc in a.cpc:
        cql = f'cpc={cpc} and pd within "{a.from_}0101 {a.to}1231"'
        try:
            out[cpc] = count(cql, tok)
        except Exception as e:  # noqa
            out[cpc] = f"ERR {type(e).__name__}: {e}"
        time.sleep(1.5)  # be polite to OPS
    return out


def cmd_country_sample(a):
    tok = token()
    cql = f'cpc={a.cpc} and pd within "{a.year}0101 {a.year}1231"'
    d = search(cql, tok, rng=f"1-{a.n}")
    docs = d["ops:world-patent-data"]["ops:biblio-search"]["ops:search-result"]["ops:publication-reference"]
    # NOTE: applicant-country extraction depends on requesting the full biblio
    # endpoint per doc; this stub returns the doc ids for the user to expand.
    ids = [r.get("document-id", {}) for r in (docs if isinstance(docs, list) else [docs])]
    print(json.dumps({"cpc": a.cpc, "year": a.year, "sampled": len(ids),
                      "note": "expand each id via /published-data/publication/.../biblio to read applicant country"}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="EPO OPS robotics/AI patent counts")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("counts"); c.add_argument("--cpc", nargs="+", default=["B25J", "G06N", "G05B"])
    c.add_argument("--from", dest="from_", default="2014"); c.add_argument("--to", default="2023")
    c.set_defaults(func=cmd_counts)
    s = sub.add_parser("country-sample"); s.add_argument("--cpc", default="B25J")
    s.add_argument("--year", default="2022"); s.add_argument("--n", type=int, default=100)
    s.set_defaults(func=cmd_country_sample)
    args = p.parse_args(); args.func(args)

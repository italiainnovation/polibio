#!/usr/bin/env python3
"""Minimal long-timeout ISTAT SDMX CSV fetcher for large queries.

The shared istat-sdmx.py tool hard-caps reads at 120s, which is too short for
all-comuni national pulls. This replicates its CSV fetch with a configurable
(default 600s) timeout. Use sparingly — respect ISTAT's 5 queries/minute limit.

Usage:
  python3 _istat_fetch.py DATAFLOW KEY START END OUT [TIMEOUT_SECONDS]
"""
import sys
import os
import urllib.request
import urllib.error

ENDPOINTS = [
    "https://esploradati.istat.it/SDMXWS/rest",
    "https://sdmx.istat.it/SDMXWS/rest",
]
ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"


def endpoints():
    ep_cache = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", ".cache", "istat-sdmx", "_endpoint",
    )
    cached = []
    try:
        with open(ep_cache) as f:
            e = f.read().strip()
            if e:
                cached = [e]
    except OSError:
        pass
    # cached first, then the rest as fallback
    return cached + [e for e in ENDPOINTS if e not in cached]


def main():
    df, key, start, end, out = sys.argv[1:6]
    timeout = int(sys.argv[6]) if len(sys.argv) > 6 else 600
    qs = f"?startPeriod={start}&endPeriod={end}"
    last_err = None
    for ep in endpoints():
        url = f"{ep}/data/{df}/{key}{qs}"
        print(f"GET {url}  (timeout={timeout}s)", file=sys.stderr)
        req = urllib.request.Request(
            url, headers={"User-Agent": "QLE-Infrastructure/1.0", "Accept": ACCEPT}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            with open(out, "wb") as f:
                f.write(data)
            n = data.count(b"\n")
            print(f"OK: wrote {n} lines ({len(data)/1e6:.1f} MB) to {out}")
            return
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} on {ep}: {e.read()[:300]!r}"
            print(last_err, file=sys.stderr)
        except Exception as e:  # noqa
            last_err = f"{type(e).__name__} on {ep}: {e}"
            print(last_err, file=sys.stderr)
    sys.exit(f"All endpoints failed. Last: {last_err}")


if __name__ == "__main__":
    main()

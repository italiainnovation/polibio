#!/usr/bin/env python3
"""Pull US County Business / employment data from BLS QCEW (no API key).

County x NAICS-3-digit, private ownership (own_code=5), agglvl 75, annual.
Both establishment counts (annual_avg_estabs) and employment (annual_avg_emplvl).
The US analog of ISTAT ASIA comune-level data, for the Layer A comparison.
"""
import csv
import io
import os
import sys
import time
import urllib.request
import urllib.error

OUT = os.path.expanduser("~/qle-data/us-districts-mirror")
os.makedirs(OUT, exist_ok=True)

MFG = ["311", "312", "313", "314", "315", "316", "321", "322", "323", "324",
       "325", "326", "327", "331", "332", "333", "334", "335", "336", "337", "339"]
SVC = ["423", "424", "484", "493", "511", "518", "541", "445", "448", "531", "721", "722"]
NAICS = MFG + SVC
YEARS = [2014]  # QCEW open-data API starts at 2014; 2022 already pulled


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "QLE-research/1.0"})
    return urllib.request.urlopen(req, timeout=90).read().decode()


def main():
    for yr in YEARS:
        recs = []
        for code in NAICS:
            url = f"https://data.bls.gov/cew/data/api/{yr}/a/industry/{code}.csv"
            try:
                txt = fetch(url)
            except urllib.error.HTTPError as e:
                print(f"  {yr} {code}: HTTP {e.code}", file=sys.stderr)
                time.sleep(0.4)
                continue
            rows = list(csv.reader(io.StringIO(txt)))
            h = {c: i for i, c in enumerate(rows[0])}
            n = 0
            for r in rows[1:]:
                if r[h["own_code"]] != "5":            # private only
                    continue
                if r[h["agglvl_code"]] != "75":         # county x 3-digit NAICS
                    continue
                fips = r[h["area_fips"]]
                if not (len(fips) == 5 and fips.isdigit()):
                    continue
                recs.append({
                    "county": fips,
                    "naics": r[h["industry_code"]],
                    "estabs": r[h["annual_avg_estabs"]],
                    "emp": r[h["annual_avg_emplvl"]],
                })
                n += 1
            print(f"  {yr} {code}: {n} county rows", file=sys.stderr)
            time.sleep(0.35)
        path = os.path.join(OUT, f"us_qcew_{yr}.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["county", "naics", "estabs", "emp"])
            w.writeheader()
            w.writerows(recs)
        print(f"{yr}: wrote {len(recs)} rows -> {path}")


if __name__ == "__main__":
    main()

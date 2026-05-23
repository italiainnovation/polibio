#!/usr/bin/env python3
"""US Census BTOS AI-adoption fetcher (open data, no API key).

Downloads the Business Trends and Outlook Survey state-level estimates and
extracts the AI-use questions ("did this business use AI in any business
function?" and the 6-month expectation), by state, latest biweekly period.

Source files (US gov, public domain):
  https://www.census.gov/hfp/btos/downloads/State.xlsx
  https://www.census.gov/hfp/btos/downloads/National.xlsx

Output: ~/qle-data/adoption-pulls/btos_ai_by_state.csv
"""
import io
import os
import sys
import urllib.request

import pandas as pd

OUT = os.path.expanduser("~/qle-data/adoption-pulls")
BASE = "https://www.census.gov/hfp/btos/downloads"
REGIONS = {"OH": "rust_belt", "MI": "rust_belt", "IN": "rust_belt", "PA": "rust_belt",
           "NY": "rust_belt", "IL": "rust_corridor", "WI": "rust_belt",
           "MO": "corridor", "OK": "corridor", "TX": "corridor",
           "NC": "carolinas", "SC": "carolinas"}


def grab(name):
    os.makedirs(OUT, exist_ok=True)
    url = f"{BASE}/{name}"
    data = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "QLE-research/1.0"}), timeout=120).read()
    path = os.path.join(OUT, f"btos_{name}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def latest_yes(df, states=None):
    pcols = sorted([c for c in df.columns if str(c).isdigit()], reverse=True)
    ai = df[df["Question"].astype(str).str.contains("Artificial Intelligence", case=False, na=False)
            & df["Answer"].astype(str).str.strip().eq("Yes")]
    rows = []
    for _, r in ai.iterrows():
        st = r.get("State", "US")
        if states is not None and st not in states:
            continue
        for p in pcols:
            v = str(r[p]).strip()
            if v.endswith("%"):
                rows.append({"geo": st, "region": REGIONS.get(st, "US"),
                             "qid": r["Question ID"], "value_pct": float(v[:-1]), "period": p})
                break
    return pd.DataFrame(rows)


def main():
    state = grab("State.xlsx")
    nat = grab("National.xlsx")
    sdf = latest_yes(pd.read_excel(state, sheet_name="Response Estimates"), set(REGIONS))
    ndf = latest_yes(pd.read_excel(nat, sheet_name="Response Estimates"))
    out = pd.concat([ndf.assign(geo="US"), sdf], ignore_index=True)
    out.to_csv(os.path.join(OUT, "btos_ai_by_state.csv"), index=False)
    print(out.to_string(index=False))
    print("\nregion means (current AI use, QID 7):")
    q7 = sdf[sdf.qid == 7.0]
    print(q7.groupby("region").value_pct.mean().round(1).to_dict())
    print(f"\nsaved -> {OUT}/btos_ai_by_state.csv")


if __name__ == "__main__":
    main()

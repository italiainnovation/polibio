---
description: Build a provenance-tracked DuckDB DataPool for a study from public sources
argument-hint: [study-name] [question]
---
Construct a case-level DataPool for: $ARGUMENTS

1. From the question, derive the variables, units, period, and geographic resolution needed.
2. **evidence-constructor** — query the most proximate sources (`CLAUDE.md` §5; `tools/`) for each variable.
3. Land everything via `tools/datapool/init_pool.py` — every observation carries a `fetch_id`; no imputation without a recorded method; raw values never overwritten; source conflicts preserved.
4. Report coverage and gaps (what is missing, and why).

Build the dataset to the question, not the question to the data.

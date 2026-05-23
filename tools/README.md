# Data tools

Provenance-first connectors to common social-science data sources. They run **standalone** (plain Python, local caching) or are called by the agents during phases A and B. Everything can land in a local DuckDB **DataPool** whose schema enforces full provenance.

There are two access paths:
- **Embedded tools** (the scripts in this folder) — for sources without an MCP server.
- **MCP servers** (configured in `.mcp.json`) — e.g. FRED, and optionally Zotero, Semantic Scholar, World Bank.

## Keys

Most sources need **no key**. Copy `.env.example` → `.env` and fill only what your study uses. Keys read from the environment; none are committed.

| Needs a key | Free registration |
|---|---|
| Companies House (`COMPANIES_HOUSE_API_KEY`) | developer.company-information.service.gov.uk |
| DART / Korea (`DART_API_KEY`) | opendart.fss.or.kr |
| EPO patents (`EPO_OPS_KEY` + `EPO_OPS_SECRET`) | developers.epo.org |
| NOMIS — *optional* (`NOMIS_API_KEY`) | nomisweb.co.uk (works without; just rate-limited) |
| Felt maps — *optional* (`FELT_API_TOKEN`) | felt.com |

## Connectors

**No key:** `istat-sdmx.py` · `istat-bulk-fetch.py` (Italy) · `ine-api.py` · `ige-api.py` · `bde-api.py` · `boe-api.py` · `seguridad-social-api.py` · `catastro-api.py` · `datos-gob-es-api.py` (Spain) · `us-qcew-fetch.py` · `btos-ai-fetch.py` (US) · `land-registry-api.py` · `charity-commission-api.py` (UK) · `boundary-fetch.py` (geographic boundaries).
**Key required:** `companies-house-api.py` · `dart-api.py` · `epo-ops-fetch.py`.
**Optional:** `felt-upload.py`.

Each script self-documents: `python3 tools/<name>.py --help`. The example geographies and codes in `--help` are illustrative — pass your own.

## DataPool (`tools/datapool/`)

```bash
python3 tools/datapool/init_pool.py <study-name>      # create a provenance-tracked DuckDB store
```

The schema (`schema.sql`) enforces, by design:
- every observation carries a `fetch_id` linking to a logged API call (NOT NULL);
- imputation requires a documented method (CHECK constraint);
- `value_raw` is never overwritten; conflicting sources are preserved;
- missing data is logged by reason.

This is the methodological core: a built dataset you can trust because the system **cannot invent a data point** — only retrieve one and record where it came from.

## Adapt the stack

These connectors reflect one research program's needs. Add the sources your questions demand and drop the rest — the architecture does not change, only the stack.

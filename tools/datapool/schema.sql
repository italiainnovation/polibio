-- DataPool Schema v1
-- Per-case DuckDB file at ~/qle-data/pools/[case]/datapool.duckdb
-- Every data point traceable to a specific API call. No silent interpolation.
-- Missing data documented as missing. Source conflicts preserved.

-- ═══════════════════════════════════════════
-- METADATA TABLES (prefixed _ to separate from data)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS _pool_meta (
    pool_id         TEXT PRIMARY KEY,
    case_name       TEXT NOT NULL,
    research_question TEXT,
    propositions    TEXT[],          -- list of propositions this pool tests
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    version         INTEGER DEFAULT 1,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS _sources (
    source_id       TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK (source_type IN (
        'mcp', 'embedded_tool', 'manual', 'web', 'derived'
    )),
    source_name     TEXT NOT NULL,      -- 'FRED', 'NOMIS', 'ISTAT SDMX', etc.
    api_endpoint    TEXT,               -- exact URL or tool command
    dataset_id      TEXT,               -- dataset identifier within source
    query_params    TEXT,               -- JSON of exact parameters used
    retrieval_date  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    data_vintage    TEXT,               -- when the source last updated the data
    license         TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS _variables (
    var_id          TEXT PRIMARY KEY,
    var_name        TEXT NOT NULL,       -- human-readable name
    construct       TEXT,               -- theoretical construct it measures
    proposition     TEXT,               -- which proposition it supports (P1, P2, etc.)
    unit            TEXT,               -- 'percent', 'GBP', 'EUR', 'count', 'index'
    frequency       TEXT CHECK (frequency IN (
        'annual', 'quarterly', 'monthly', 'weekly', 'daily', 'irregular'
    )),
    geo_level       TEXT,               -- 'national', 'NUTS1', 'NUTS2', 'NUTS3',
                                        -- 'local_authority', 'municipality', 'county',
                                        -- 'tract', 'province', 'CCAA'
    geo_codes       TEXT[],             -- list of geography codes covered
    time_start      DATE,
    time_end        DATE,
    source_id       TEXT REFERENCES _sources(source_id),
    quality_score   REAL CHECK (quality_score BETWEEN 0.0 AND 1.0),
    coverage_pct    REAL CHECK (coverage_pct BETWEEN 0.0 AND 100.0),
    is_derived      BOOLEAN DEFAULT FALSE,
    derived_from    TEXT[],             -- var_ids this was derived from
    transform_desc  TEXT,               -- human-readable description of derivation
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS _fetch_log (
    fetch_id        TEXT PRIMARY KEY,
    source_id       TEXT REFERENCES _sources(source_id),
    var_id          TEXT,               -- which variable this fetch populates
    fetched_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    command         TEXT NOT NULL,       -- exact API call or tool command executed
    raw_row_count   INTEGER,
    raw_col_count   INTEGER,
    status          TEXT NOT NULL CHECK (status IN (
        'success', 'partial', 'failed', 'rate_limited', 'unavailable'
    )),
    error_message   TEXT,               -- populated if status != 'success'
    raw_file_path   TEXT,               -- path to preserved raw response
    checksum        TEXT,               -- SHA256 of raw response file
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS _transforms (
    transform_id    TEXT PRIMARY KEY,
    var_id          TEXT,               -- variable affected
    applied_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transform_type  TEXT NOT NULL CHECK (transform_type IN (
        'unit_conversion', 'date_normalization', 'geo_crosswalk',
        'aggregation', 'deflation', 'index_rebasing',
        'imputation', 'custom'
    )),
    description     TEXT NOT NULL,       -- human-readable
    code            TEXT NOT NULL,       -- executable Python or SQL reproducing this
    input_checksum  TEXT,
    output_checksum TEXT,
    approved_by     TEXT                 -- NULL = safe auto-transform;
                                         -- researcher name = judgment call
);

CREATE TABLE IF NOT EXISTS _conflicts (
    conflict_id     TEXT PRIMARY KEY,
    var_name        TEXT NOT NULL,
    geo_code        TEXT,
    time_period     DATE,
    source_a_id     TEXT REFERENCES _sources(source_id),
    source_b_id     TEXT REFERENCES _sources(source_id),
    value_a         REAL,
    value_b         REAL,
    difference_pct  REAL,               -- abs(a-b)/avg(a,b) * 100
    resolution      TEXT DEFAULT 'unresolved' CHECK (resolution IN (
        'unresolved', 'source_a', 'source_b', 'averaged', 'excluded', 'researcher_override'
    )),
    resolution_reason TEXT,
    resolved_by     TEXT,               -- researcher name if manually resolved
    detected_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS _missing (
    missing_id      TEXT PRIMARY KEY,
    var_id          TEXT REFERENCES _variables(var_id),
    geo_code        TEXT,
    time_start      DATE,
    time_end        DATE,
    reason          TEXT NOT NULL CHECK (reason IN (
        'source_gap',           -- source exists but doesn't cover this period
        'geo_mismatch',         -- source doesn't cover this geography
        'temporal_gap',         -- gap within an otherwise available series
        'not_collected',        -- indicator not collected for this unit
        'suppressed',           -- data exists but suppressed (privacy, small N)
        'pre_series',           -- series starts after requested period
        'post_series',          -- series ends before requested period
        'api_failure',          -- fetch attempted but failed
        'rate_limited',         -- couldn't retrieve due to rate limits
        'not_found'             -- searched all sources, indicator doesn't exist
    )),
    sources_checked TEXT[],             -- which sources were queried
    notes           TEXT,
    logged_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════
-- DATA TABLE: Long/tidy format, canonical store
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS observations (
    var_id          TEXT NOT NULL,
    geo_code        TEXT NOT NULL,
    geo_name        TEXT,
    time_period     DATE NOT NULL,
    time_grain      TEXT NOT NULL CHECK (time_grain IN ('Y','Q','M','W','D')),
    value_raw       REAL,               -- original from source, NEVER overwritten
    value_clean     REAL,               -- after transforms; NULL if no transform applied
    is_imputed      BOOLEAN DEFAULT FALSE,
    imputation_method TEXT,             -- required if is_imputed=TRUE
    fetch_id        TEXT NOT NULL REFERENCES _fetch_log(fetch_id),
    PRIMARY KEY (var_id, geo_code, time_period),
    -- Anti-fabrication: imputation requires method documentation
    CHECK (is_imputed = FALSE OR imputation_method IS NOT NULL)
);

-- ═══════════════════════════════════════════
-- CONVENIENCE VIEWS
-- ═══════════════════════════════════════════

-- Full provenance chain: observation → fetch → source
CREATE VIEW v_provenance AS
SELECT
    o.var_id,
    v.var_name,
    v.construct,
    v.proposition,
    o.geo_code,
    o.geo_name,
    o.time_period,
    o.time_grain,
    o.value_raw,
    o.value_clean,
    o.is_imputed,
    o.imputation_method,
    f.command AS fetch_command,
    f.fetched_at,
    f.raw_file_path,
    f.checksum AS raw_checksum,
    s.source_name,
    s.source_type,
    s.api_endpoint,
    s.retrieval_date
FROM observations o
JOIN _fetch_log f ON o.fetch_id = f.fetch_id
LEFT JOIN _sources s ON f.source_id = s.source_id
LEFT JOIN _variables v ON o.var_id = v.var_id;

-- Variable quality summary
CREATE VIEW v_quality AS
SELECT
    v.var_id,
    v.var_name,
    v.construct,
    v.proposition,
    v.source_id,
    s.source_name,
    v.frequency,
    v.geo_level,
    v.time_start,
    v.time_end,
    v.coverage_pct,
    v.quality_score,
    v.is_derived,
    COUNT(DISTINCT o.geo_code) AS geo_count,
    COUNT(o.time_period) AS obs_count,
    SUM(CASE WHEN o.is_imputed THEN 1 ELSE 0 END) AS imputed_count,
    COUNT(DISTINCT m.missing_id) AS missing_count,
    COUNT(DISTINCT c.conflict_id) AS conflict_count
FROM _variables v
LEFT JOIN _sources s ON v.source_id = s.source_id
LEFT JOIN observations o ON v.var_id = o.var_id
LEFT JOIN _missing m ON v.var_id = m.var_id
LEFT JOIN _conflicts c ON v.var_name = c.var_name
GROUP BY v.var_id, v.var_name, v.construct, v.proposition,
         v.source_id, s.source_name, v.frequency, v.geo_level,
         v.time_start, v.time_end, v.coverage_pct, v.quality_score,
         v.is_derived;

-- Pool health dashboard
CREATE VIEW v_pool_health AS
SELECT
    (SELECT COUNT(*) FROM _variables) AS total_variables,
    (SELECT COUNT(*) FROM observations) AS total_observations,
    (SELECT COUNT(DISTINCT source_id) FROM _sources) AS total_sources,
    (SELECT COUNT(*) FROM _conflicts WHERE resolution = 'unresolved') AS unresolved_conflicts,
    (SELECT COUNT(*) FROM _missing) AS documented_gaps,
    (SELECT COUNT(*) FROM observations WHERE is_imputed = TRUE) AS imputed_values,
    (SELECT AVG(coverage_pct) FROM _variables WHERE coverage_pct IS NOT NULL) AS avg_coverage_pct,
    (SELECT COUNT(*) FROM _variables WHERE coverage_pct < 50) AS low_coverage_vars,
    (SELECT COUNT(*) FROM _fetch_log WHERE status != 'success') AS failed_fetches;

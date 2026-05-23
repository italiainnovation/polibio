#!/usr/bin/env python3
"""
DataPool Initialization & Management Tool

Creates, inspects, exports, and deploys DataPool databases.

Usage:
    python3 init_pool.py <case-name> ["research question"]   # create pool
    python3 init_pool.py <case-name> --status                # health summary
    python3 init_pool.py <case-name> --verify                # anti-fabrication checks
    python3 init_pool.py <case-name> --export                # export JSON + deploy to app
    python3 init_pool.py --list                              # list all pools

The --export command is the key step after loading data:
  1. Reads the DuckDB database
  2. Exports all tables and views as JSON to app/data/pools/[case]/
  3. Regenerates manifest.json with actual variable/observation counts
  4. Updates app/data/pools/registry.json
  → Ready to git add + commit + push

Anti-fabrication guarantees:
    - Every observation traces to a fetch_log entry (NOT NULL constraint)
    - Imputation requires documented method (CHECK constraint)
    - value_raw is never overwritten (schema design)
    - Source conflicts preserved with both values
    - Missing data documented with reason and sources checked
"""

import sys
import os
import json
import hashlib
from datetime import datetime
from pathlib import Path

POOLS_DIR = Path.home() / "qle-data" / "pools"
REGISTRY_FILE = POOLS_DIR / "registry.json"
SCHEMA_FILE = Path(__file__).parent / "schema.sql"
# Project root — for exporting to app/data/pools/
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def ensure_dirs():
    """Create pool infrastructure directories."""
    POOLS_DIR.mkdir(parents=True, exist_ok=True)


def load_registry():
    """Load or create the pool registry."""
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    return {"pools": [], "schema_version": 1}


def save_registry(registry):
    """Persist the registry."""
    with open(REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2, default=str)


def init_pool(case_name, research_question=None):
    """Create a new DataPool for a case."""
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    ensure_dirs()
    pool_dir = POOLS_DIR / case_name
    db_path = pool_dir / "datapool.duckdb"
    raw_dir = pool_dir / "raw"
    provenance_dir = pool_dir / "provenance"

    # Create directories
    pool_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(exist_ok=True)
    provenance_dir.mkdir(exist_ok=True)

    # Check if pool already exists
    if db_path.exists():
        print(f"Pool already exists at {db_path}")
        print("Use --update to add variables, or delete the pool first.")
        return

    # Read schema
    if not SCHEMA_FILE.exists():
        print(f"ERROR: Schema file not found at {SCHEMA_FILE}")
        sys.exit(1)

    with open(SCHEMA_FILE) as f:
        schema_sql = f.read()

    # Create DuckDB with schema
    con = duckdb.connect(str(db_path))

    # Execute schema — use DuckDB's built-in multi-statement support
    # Strip SQL comments first to avoid issues
    import re
    clean_sql = re.sub(r'--[^\n]*', '', schema_sql)
    try:
        con.execute(clean_sql)
    except Exception as e:
        # Fall back to statement-by-statement for debugging
        print(f"Bulk execute failed: {e}")
        print("Trying statement-by-statement...")
        con.close()
        os.remove(str(db_path))
        con = duckdb.connect(str(db_path))
        # Parse statements respecting parentheses
        stmts = []
        current = []
        paren_depth = 0
        for line in clean_sql.split('\n'):
            line = line.strip()
            if not line:
                continue
            paren_depth += line.count('(') - line.count(')')
            current.append(line)
            if paren_depth == 0 and line.endswith(';'):
                stmts.append(' '.join(current))
                current = []
        if current:
            stmts.append(' '.join(current))
        for stmt in stmts:
            stmt = stmt.strip().rstrip(';').strip()
            if not stmt:
                continue
            try:
                con.execute(stmt)
            except Exception as e2:
                print(f"Schema error on: {stmt[:120]}...")
                print(f"Error: {e2}")
                con.close()
                db_path.unlink(missing_ok=True)
                sys.exit(1)

    # Insert pool metadata
    pool_id = f"{case_name}-{datetime.now().strftime('%Y%m%d')}"
    now = datetime.now().isoformat()
    con.execute("""
        INSERT INTO _pool_meta (pool_id, case_name, research_question, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, [pool_id, case_name, research_question, now, now])

    con.close()

    # Compute schema checksum for reproducibility
    schema_hash = hashlib.sha256(schema_sql.encode()).hexdigest()[:16]

    # Update registry
    registry = load_registry()
    registry["pools"] = [p for p in registry["pools"] if p["case"] != case_name]
    registry["pools"].append({
        "case": case_name,
        "pool_id": pool_id,
        "db_path": str(db_path),
        "created_at": now,
        "research_question": research_question,
        "schema_checksum": schema_hash,
        "variables": 0,
        "observations": 0,
        "sources": 0
    })
    save_registry(registry)

    # Create manifest stub
    manifest = {
        "pool_id": pool_id,
        "case": case_name,
        "schema_version": 1,
        "schema_checksum": schema_hash,
        "created_at": now,
        "research_question": research_question,
        "variables": [],
        "sources": [],
        "quality": {
            "total_observations": 0,
            "avg_coverage_pct": None,
            "unresolved_conflicts": 0,
            "documented_gaps": 0,
            "imputed_values": 0
        },
        "anti_fabrication": {
            "all_observations_have_fetch_id": True,
            "no_hidden_imputation": True,
            "raw_values_immutable": True,
            "all_transforms_logged": True,
            "conflicts_preserved": True,
            "missing_data_documented": True
        }
    }
    with open(pool_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Create dictionary stub
    dict_content = f"""# DataPool Dictionary: {case_name}

**Pool ID:** {pool_id}
**Created:** {now}
**Research Question:** {research_question or '(not specified)'}
**Schema Checksum:** {schema_hash}

## Anti-Fabrication Guarantees

1. Every observation traces to a specific API call via `fetch_id` (NOT NULL constraint)
2. Imputation requires documented method (`is_imputed = TRUE` requires `imputation_method IS NOT NULL`)
3. Raw values (`value_raw`) are never overwritten — transforms write to `value_clean` only
4. Every transformation logged with executable code in `_transforms`
5. Source conflicts preserved with both values in `_conflicts` (default: unresolved)
6. Missing data documented in `_missing` with reason and sources checked
7. Raw response files preserved in `raw/` with SHA256 checksums

## Variables

*No variables assembled yet. Run `/construct-datapool {case_name}` to populate.*

## Sources

*No sources queried yet.*

## Quality Summary

*Pool is empty. Quality metrics will appear after data assembly.*
"""
    with open(pool_dir / "dictionary.md", "w") as f:
        f.write(dict_content)

    print(f"DataPool created for '{case_name}'")
    print(f"  Database: {db_path}")
    print(f"  Raw files: {raw_dir}")
    print(f"  Manifest: {pool_dir / 'manifest.json'}")
    print(f"  Dictionary: {pool_dir / 'dictionary.md'}")
    print(f"  Schema checksum: {schema_hash}")
    if research_question:
        print(f"  Question: {research_question}")


def list_pools():
    """List all existing DataPools."""
    registry = load_registry()
    if not registry["pools"]:
        print("No DataPools exist yet. Create one with:")
        print("  python3 init_pool.py <case-name> [\"research question\"]")
        return

    print(f"{'Case':<25} {'Variables':<10} {'Observations':<14} {'Sources':<9} {'Created'}")
    print("-" * 80)
    for p in sorted(registry["pools"], key=lambda x: x["case"]):
        print(f"{p['case']:<25} {p.get('variables', 0):<10} "
              f"{p.get('observations', 0):<14} {p.get('sources', 0):<9} "
              f"{p['created_at'][:10]}")


def pool_status(case_name):
    """Show pool health and anti-fabrication status."""
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    db_path = POOLS_DIR / case_name / "datapool.duckdb"
    if not db_path.exists():
        print(f"No DataPool found for '{case_name}'")
        return

    con = duckdb.connect(str(db_path), read_only=True)

    try:
        health = con.execute("SELECT * FROM v_pool_health").fetchone()
        if health:
            labels = [
                "Total variables", "Total observations", "Total sources",
                "Unresolved conflicts", "Documented gaps", "Imputed values",
                "Avg coverage %", "Low coverage vars", "Failed fetches"
            ]
            print(f"\nDataPool Health: {case_name}")
            print("=" * 40)
            for label, val in zip(labels, health):
                if val is None:
                    val = "N/A"
                elif isinstance(val, float):
                    val = f"{val:.1f}"
                print(f"  {label:<25} {val}")
    except Exception as e:
        print(f"Error reading pool: {e}")
    finally:
        con.close()


def verify_pool(case_name):
    """Run anti-fabrication verification checks."""
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    db_path = POOLS_DIR / case_name / "datapool.duckdb"
    if not db_path.exists():
        print(f"No DataPool found for '{case_name}'")
        return

    con = duckdb.connect(str(db_path), read_only=True)
    checks = []

    # Check 1: All observations have fetch_id
    result = con.execute(
        "SELECT COUNT(*) FROM observations WHERE fetch_id IS NULL"
    ).fetchone()[0]
    checks.append(("All observations have fetch_id", result == 0, result))

    # Check 2: No hidden imputation
    result = con.execute("""
        SELECT COUNT(*) FROM observations
        WHERE is_imputed = TRUE AND imputation_method IS NULL
    """).fetchone()[0]
    checks.append(("No hidden imputation", result == 0, result))

    # Check 3: All transforms logged
    result = con.execute("""
        SELECT COUNT(*) FROM observations o
        WHERE o.value_clean IS NOT NULL
        AND o.value_raw != o.value_clean
        AND NOT EXISTS (
            SELECT 1 FROM _transforms t WHERE t.var_id = o.var_id
        )
    """).fetchone()[0]
    checks.append(("All transforms logged", result == 0, result))

    # Check 4: No orphan fetch_ids
    result = con.execute("""
        SELECT COUNT(*) FROM observations o
        WHERE NOT EXISTS (
            SELECT 1 FROM _fetch_log f WHERE f.fetch_id = o.fetch_id
        )
    """).fetchone()[0]
    checks.append(("No orphan fetch references", result == 0, result))

    # Check 5: Conflicts detected
    result = con.execute(
        "SELECT COUNT(*) FROM _conflicts WHERE resolution = 'unresolved'"
    ).fetchone()[0]
    checks.append(("No unresolved conflicts", result == 0, result))

    con.close()

    print(f"\nAnti-Fabrication Verification: {case_name}")
    print("=" * 50)
    all_pass = True
    for name, passed, count in checks:
        status = "PASS" if passed else "FAIL"
        icon = "+" if passed else "!"
        suffix = "" if passed else f" ({count} violations)"
        print(f"  [{icon}] {status}: {name}{suffix}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  All anti-fabrication checks passed.")
    else:
        print("  WARNING: Some checks failed. Review before using this pool.")


def export_pool(case_name):
    """Export DuckDB pool to JSON files in app/data/pools/ for deployment.

    This is the critical step between loading data and deploying:
    1. Reads every table and view from the DuckDB file
    2. Writes JSON exports to app/data/pools/[case]/
    3. Regenerates manifest.json with actual counts
    4. Updates app/data/pools/registry.json

    After this, just: git add app/data/pools/ && git commit && git push
    """
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    db_path = POOLS_DIR / case_name / "datapool.duckdb"
    if not db_path.exists():
        print(f"No DataPool found for '{case_name}'")
        sys.exit(1)

    app_pool_dir = PROJECT_ROOT / "app" / "data" / "pools" / case_name
    app_pool_dir.mkdir(parents=True, exist_ok=True)
    (app_pool_dir / "raw").mkdir(exist_ok=True)
    (app_pool_dir / "provenance").mkdir(exist_ok=True)

    con = duckdb.connect(str(db_path), read_only=True)

    # Export tables
    tables = [
        ("_pool_meta", "pool_meta.json"),
        ("_sources", "sources.json"),
        ("_variables", "variables.json"),
        ("_fetch_log", "fetch_log.json"),
        ("_transforms", "transforms.json"),
        ("_conflicts", "conflicts.json"),
        ("_missing", "missing.json"),
        ("observations", "observations.json"),
    ]
    views = [
        ("v_pool_health", "v_pool_health.json"),
        ("v_quality", "v_quality.json"),
        ("v_provenance", "v_provenance.json"),
    ]

    for tbl, filename in tables + views:
        try:
            df = con.execute(f"SELECT * FROM {tbl}").fetchdf()
            data = json.loads(df.to_json(orient="records", date_format="iso"))
            with open(app_pool_dir / filename, "w") as f:
                json.dump(data, f, indent=2, default=str)
            print(f"  {tbl}: {len(data)} rows → {filename}")
        except Exception as e:
            print(f"  {tbl}: SKIP ({e})")

    # Regenerate manifest from actual data
    health = con.execute("SELECT * FROM v_pool_health").fetchone()
    variables = con.execute("SELECT var_id FROM _variables").fetchall()
    sources = con.execute("SELECT source_id FROM _sources").fetchall()
    meta = con.execute("SELECT * FROM _pool_meta").fetchone()

    manifest = {
        "pool_id": meta[0] if meta else f"{case_name}-export",
        "case": case_name,
        "schema_version": 1,
        "created_at": meta[4] if meta else datetime.now().isoformat(),
        "research_question": meta[2] if meta else "",
        "variables": [v[0] for v in variables],
        "sources": [s[0] for s in sources],
        "quality": {
            "total_observations": health[1] if health else 0,
            "avg_coverage_pct": health[6] if health else None,
            "unresolved_conflicts": health[3] if health else 0,
            "documented_gaps": health[4] if health else 0,
            "imputed_values": health[5] if health else 0,
        },
    }
    with open(app_pool_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    print(f"  manifest.json regenerated: {len(variables)} vars, {health[1] if health else 0} obs")

    # Copy dictionary
    dict_src = POOLS_DIR / case_name / "dictionary.md"
    if dict_src.exists():
        import shutil
        shutil.copy(str(dict_src), str(app_pool_dir / "dictionary.md"))

    con.close()

    # Update app registry
    app_registry_path = PROJECT_ROOT / "app" / "data" / "pools" / "registry.json"
    if app_registry_path.exists():
        app_reg = json.load(open(app_registry_path))
    else:
        app_reg = {"pools": [], "schema_version": 1}

    app_reg["pools"] = [p for p in app_reg["pools"] if p["case"] != case_name]
    app_reg["pools"].append({
        "case": case_name,
        "pool_id": manifest["pool_id"],
        "db_path": f"{case_name}/datapool.duckdb",
        "created_at": manifest["created_at"],
        "research_question": manifest["research_question"],
        "variables": len(variables),
        "observations": health[1] if health else 0,
        "sources": len(sources),
    })
    with open(app_registry_path, "w") as f:
        json.dump(app_reg, f, indent=2, default=str)
    print(f"  registry.json updated ({len(app_reg['pools'])} pools)")

    print(f"\nExported to: {app_pool_dir}")
    print(f"Now run:")
    print(f"  cd app && git add -f data/pools/ && git commit -m 'Update {case_name} DataPool' && git push")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 init_pool.py <case-name> [\"research question\"]")
        print("       python3 init_pool.py --list")
        print("       python3 init_pool.py <case-name> --status")
        print("       python3 init_pool.py <case-name> --verify")
        print("       python3 init_pool.py <case-name> --export")
        sys.exit(1)

    if sys.argv[1] == "--list":
        list_pools()
        return

    case_name = sys.argv[1]

    if len(sys.argv) > 2 and sys.argv[2] == "--status":
        pool_status(case_name)
        return

    if len(sys.argv) > 2 and sys.argv[2] == "--verify":
        verify_pool(case_name)
        return

    if len(sys.argv) > 2 and sys.argv[2] == "--export":
        export_pool(case_name)
        return

    research_question = sys.argv[2] if len(sys.argv) > 2 else None
    init_pool(case_name, research_question)


if __name__ == "__main__":
    main()

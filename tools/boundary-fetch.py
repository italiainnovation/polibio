#!/usr/bin/env python3
"""Fetch official jurisdiction boundary polygons as GeoJSON.

US boundaries come from the Census Bureau TIGER/Line ArcGIS REST API.
International boundaries come from Natural Earth (10m resolution).

Usage:
    # US county
    python3 tools/boundary-fetch.py us-county --state 06 --county 085

    # US city/place
    python3 tools/boundary-fetch.py us-place --state 06 --name "Palo Alto"

    # US state
    python3 tools/boundary-fetch.py us-state --state 06

    # US census tract
    python3 tools/boundary-fetch.py us-tract --state 06 --county 085 --tract 504323

    # US congressional district
    python3 tools/boundary-fetch.py us-congress --state 06 --district 18

    # International country
    python3 tools/boundary-fetch.py country --name "Italy"

    # International admin-1 (state/province/region)
    python3 tools/boundary-fetch.py admin1 --country "Italy" --name "Lombardia"

    # Write output to a file
    python3 tools/boundary-fetch.py us-county --state 06 --county 085 -o boundary.geojson

    # Add QLE properties for Felt integration
    python3 tools/boundary-fetch.py us-county --state 06 --county 085 \
        --category governance-boundary --causal-step "Step 1" --notes "Treatment area"

Output:
    GeoJSON FeatureCollection to stdout (or file with -o). Each feature
    includes the original source properties plus any QLE properties added
    via flags. Compatible with Felt drag-and-drop and felt-upload.py.

No external dependencies — uses only Python stdlib.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# TIGER/Line ArcGIS REST API
# ---------------------------------------------------------------------------

TIGER_BASE = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/tigerWMS_Current/MapServer"
)

# Layer IDs in tigerWMS_Current
TIGER_LAYERS = {
    "state": 80,
    "county": 82,
    "tract": 8,
    "block_group": 10,
    "place": 28,         # Incorporated places (cities, towns, villages)
    "cdp": 30,           # Census Designated Places
    "cousub": 22,        # County subdivisions (townships, etc.)
    "congress": 54,      # Congressional districts (118th)
    "state_upper": 56,   # State senate districts
    "state_lower": 58,   # State house districts
    "zcta": 2,           # ZIP Code Tabulation Areas
    "cbsa": 92,          # Core Based Statistical Areas (metro/micro)
    "urban_area": 96,    # Urban areas
    "school_unified": 14, # Unified school districts
}


def _tiger_query(layer_id, where_clause, out_fields="*"):
    """Query a TIGER layer and return a GeoJSON FeatureCollection."""
    params = urllib.parse.urlencode({
        "where": where_clause,
        "outFields": out_fields,
        "f": "geojson",
        "outSR": "4326",
        "returnGeometry": "true",
    })
    url = "{}/{}/query?{}".format(TIGER_BASE, layer_id, params)
    req = urllib.request.Request(url, headers={"User-Agent": "qle-boundary-fetch/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.load(resp)
    except urllib.error.HTTPError as e:
        print("TIGER API error: {} {}".format(e.code, e.reason), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("TIGER API request failed: {}".format(e), file=sys.stderr)
        sys.exit(1)

    if not data.get("features"):
        print("No features returned. Check your query parameters.", file=sys.stderr)
        print("Query: {} (layer {})".format(where_clause, layer_id), file=sys.stderr)
        sys.exit(1)

    return data


def fetch_us_state(state_fips):
    """Fetch a US state boundary. state_fips: 2-digit FIPS code (e.g. '06')."""
    return _tiger_query(
        TIGER_LAYERS["state"],
        "STATE='{}'".format(state_fips.zfill(2)),
        "NAME,STATE,GEOID",
    )


def fetch_us_county(state_fips, county_fips):
    """Fetch a US county boundary. county_fips: 3-digit FIPS code (e.g. '085')."""
    return _tiger_query(
        TIGER_LAYERS["county"],
        "STATE='{}' AND COUNTY='{}'".format(state_fips.zfill(2), county_fips.zfill(3)),
        "NAME,STATE,COUNTY,GEOID",
    )


def fetch_us_place(state_fips, place_name):
    """Fetch a US incorporated place (city/town) by name within a state."""
    # BASENAME is the plain name without type suffix
    return _tiger_query(
        TIGER_LAYERS["place"],
        "BASENAME='{}' AND STATE='{}'".format(
            place_name.replace("'", "''"),
            state_fips.zfill(2),
        ),
        "NAME,BASENAME,STATE,PLACE,GEOID",
    )


def fetch_us_cdp(state_fips, place_name):
    """Fetch a Census Designated Place by name within a state."""
    return _tiger_query(
        TIGER_LAYERS["cdp"],
        "BASENAME='{}' AND STATE='{}'".format(
            place_name.replace("'", "''"),
            state_fips.zfill(2),
        ),
        "NAME,BASENAME,STATE,PLACE,GEOID",
    )


def fetch_us_tract(state_fips, county_fips, tract_code):
    """Fetch a census tract. tract_code: 6-digit code (e.g. '504323')."""
    return _tiger_query(
        TIGER_LAYERS["tract"],
        "STATE='{}' AND COUNTY='{}' AND TRACT='{}'".format(
            state_fips.zfill(2),
            county_fips.zfill(3),
            tract_code.zfill(6),
        ),
        "NAME,STATE,COUNTY,TRACT,GEOID",
    )


def fetch_us_congress(state_fips, district):
    """Fetch a congressional district boundary."""
    return _tiger_query(
        TIGER_LAYERS["congress"],
        "STATE='{}' AND CD='{}'".format(
            state_fips.zfill(2),
            district.zfill(2),
        ),
        "NAME,STATE,CD,GEOID",
    )


def fetch_us_cousub(state_fips, county_fips, name):
    """Fetch a county subdivision (township, borough, etc.)."""
    return _tiger_query(
        TIGER_LAYERS["cousub"],
        "BASENAME='{}' AND STATE='{}' AND COUNTY='{}'".format(
            name.replace("'", "''"),
            state_fips.zfill(2),
            county_fips.zfill(3),
        ),
        "NAME,BASENAME,STATE,COUNTY,COUSUB,GEOID",
    )


# ---------------------------------------------------------------------------
# Natural Earth (international boundaries)
# ---------------------------------------------------------------------------

NE_GITHUB_BASE = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson"
)

# Cache directory for downloaded Natural Earth files
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".cache",
    "natural-earth",
)


def _ne_cache_path(filename):
    return os.path.join(_CACHE_DIR, filename)


def _download_ne(filename):
    """Download a Natural Earth GeoJSON file, with local caching."""
    cache_path = _ne_cache_path(filename)
    if os.path.isfile(cache_path):
        with open(cache_path, "r") as f:
            return json.load(f)

    url = "{}/{}".format(NE_GITHUB_BASE, filename)
    print("Downloading {} ...".format(filename), file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "qle-boundary-fetch/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        raw = resp.read()
    except Exception as e:
        print("Natural Earth download failed: {}".format(e), file=sys.stderr)
        sys.exit(1)

    data = json.loads(raw)

    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(raw)
    print("Cached at {}".format(cache_path), file=sys.stderr)

    return data


def _ne_filter(data, match_fn):
    """Filter a Natural Earth FeatureCollection by a match function."""
    matches = [f for f in data["features"] if match_fn(f["properties"])]
    if not matches:
        print("No matching features found in Natural Earth data.", file=sys.stderr)
        sys.exit(1)
    return {"type": "FeatureCollection", "features": matches}


def _safe(props, key):
    """Get a property as lowercase string, safe against None values."""
    val = props.get(key)
    return val.lower() if val else ""


def fetch_country(country_name):
    """Fetch a country boundary from Natural Earth 10m admin-0."""
    data = _download_ne("ne_10m_admin_0_countries.geojson")
    name_lower = country_name.lower()
    return _ne_filter(data, lambda p: (
        name_lower in _safe(p, "NAME")
        or name_lower in _safe(p, "ADMIN")
        or name_lower in _safe(p, "SOVEREIGNT")
        or name_lower == _safe(p, "ADM0_A3")
        or name_lower == _safe(p, "ISO_A2")
        or name_lower == _safe(p, "ISO_A3")
    ))


def fetch_admin1(country_name, admin1_name):
    """Fetch a first-level admin region (state/province/region).

    Note: Natural Earth stores some countries at province level rather than
    region level (e.g. Italy has provinces, not regions). When the name
    matches multiple features (e.g. all provinces in Lombardia), all are
    returned so they can be viewed together as a region layer.
    """
    data = _download_ne("ne_10m_admin_1_states_provinces.geojson")
    country_lower = country_name.lower()
    admin_lower = admin1_name.lower()

    def _country_match(p):
        return (
            country_lower in _safe(p, "admin")
            or country_lower in _safe(p, "sovereignt")
            or country_lower == _safe(p, "iso_a2")
            or country_lower == _safe(p, "adm0_a3")
        )

    def _name_match(p):
        return (
            admin_lower in _safe(p, "name")
            or admin_lower in _safe(p, "name_en")
            or admin_lower == _safe(p, "iso_3166_2")
            # Match by region name (groups provinces under a region)
            or admin_lower in _safe(p, "region")
            or admin_lower == _safe(p, "region_cod")
            or (admin_lower in _safe(p, "name_local") if p.get("name_local") else False)
        )

    return _ne_filter(data, lambda p: _country_match(p) and _name_match(p))


# ---------------------------------------------------------------------------
# QLE property enrichment
# ---------------------------------------------------------------------------

def enrich_features(geojson, category=None, causal_step=None, priority=None,
                    time_recommendation=None, notes=None):
    """Add QLE-standard properties to each feature for Felt integration."""
    qle = {}
    if category:
        qle["category"] = category
    if causal_step:
        qle["causal_step"] = causal_step
    if priority:
        qle["priority"] = priority
    if time_recommendation:
        qle["time_recommendation"] = time_recommendation
    if notes:
        qle["notes"] = notes

    if qle:
        for feature in geojson.get("features", []):
            # Ensure 'name' exists from the source
            props = feature.get("properties", {})
            if "name" not in props:
                for key in ("NAME", "BASENAME", "ADMIN", "admin"):
                    if key in props:
                        props["name"] = props[key]
                        break
            props.update(qle)
            feature["properties"] = props

    return geojson


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch official jurisdiction boundary polygons as GeoJSON",
        epilog=(
            "US boundaries: Census Bureau TIGER/Line API. "
            "International: Natural Earth 10m."
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Boundary type")
    sub.required = True

    # --- US state ---
    p_state = sub.add_parser("us-state", help="US state boundary")
    p_state.add_argument("--state", required=True, help="2-digit FIPS code (e.g. 06)")

    # --- US county ---
    p_county = sub.add_parser("us-county", help="US county boundary")
    p_county.add_argument("--state", required=True, help="2-digit FIPS code")
    p_county.add_argument("--county", required=True, help="3-digit FIPS code (e.g. 085)")

    # --- US place (city) ---
    p_place = sub.add_parser("us-place", help="US incorporated place (city/town)")
    p_place.add_argument("--state", required=True, help="2-digit FIPS code")
    p_place.add_argument("--name", required=True, help="Place name (e.g. 'Palo Alto')")

    # --- US CDP ---
    p_cdp = sub.add_parser("us-cdp", help="US Census Designated Place")
    p_cdp.add_argument("--state", required=True, help="2-digit FIPS code")
    p_cdp.add_argument("--name", required=True, help="CDP name (e.g. 'East Palo Alto')")

    # --- US census tract ---
    p_tract = sub.add_parser("us-tract", help="US census tract boundary")
    p_tract.add_argument("--state", required=True, help="2-digit FIPS code")
    p_tract.add_argument("--county", required=True, help="3-digit FIPS code")
    p_tract.add_argument("--tract", required=True, help="6-digit tract code (e.g. 504323)")

    # --- US congressional district ---
    p_cong = sub.add_parser("us-congress", help="US congressional district")
    p_cong.add_argument("--state", required=True, help="2-digit FIPS code")
    p_cong.add_argument("--district", required=True, help="2-digit district number")

    # --- US county subdivision ---
    p_cousub = sub.add_parser("us-cousub", help="US county subdivision (township, etc.)")
    p_cousub.add_argument("--state", required=True, help="2-digit FIPS code")
    p_cousub.add_argument("--county", required=True, help="3-digit FIPS code")
    p_cousub.add_argument("--name", required=True, help="Subdivision name")

    # --- Country ---
    p_country = sub.add_parser("country", help="International country boundary")
    p_country.add_argument("--name", required=True, help="Country name or ISO code")

    # --- Admin-1 ---
    p_admin1 = sub.add_parser("admin1", help="International admin-1 (state/province/region)")
    p_admin1.add_argument("--country", required=True, help="Country name or ISO code")
    p_admin1.add_argument("--name", required=True, help="Region/province/state name")

    # --- Common options ---
    for p in [p_state, p_county, p_place, p_cdp, p_tract, p_cong, p_cousub,
              p_country, p_admin1]:
        p.add_argument("-o", "--output", help="Output file (default: stdout)")
        p.add_argument("--category", help="QLE category property")
        p.add_argument("--causal-step", help="QLE causal_step property")
        p.add_argument("--priority", help="QLE priority property")
        p.add_argument("--time-recommendation", help="QLE time_recommendation property")
        p.add_argument("--notes", help="QLE notes property")

    args = parser.parse_args()

    # Dispatch
    if args.command == "us-state":
        result = fetch_us_state(args.state)
    elif args.command == "us-county":
        result = fetch_us_county(args.state, args.county)
    elif args.command == "us-place":
        result = fetch_us_place(args.state, args.name)
    elif args.command == "us-cdp":
        result = fetch_us_cdp(args.state, args.name)
    elif args.command == "us-tract":
        result = fetch_us_tract(args.state, args.county, args.tract)
    elif args.command == "us-congress":
        result = fetch_us_congress(args.state, args.district)
    elif args.command == "us-cousub":
        result = fetch_us_cousub(args.state, args.county, args.name)
    elif args.command == "country":
        result = fetch_country(args.name)
    elif args.command == "admin1":
        result = fetch_admin1(args.country, args.name)

    # Enrich with QLE properties
    result = enrich_features(
        result,
        category=args.category,
        causal_step=getattr(args, "causal_step", None),
        priority=args.priority,
        time_recommendation=getattr(args, "time_recommendation", None),
        notes=args.notes,
    )

    # Output
    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        n = len(result.get("features", []))
        print("Wrote {} feature(s) to {}".format(n, args.output), file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

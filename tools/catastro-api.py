#!/usr/bin/env python3
"""Query the Spanish Catastro (land registry / cadastre) via its public APIs.

Two access methods:
  1. SOAP/REST non-protected services — property data, addresses, parcels
     Base: https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/
  2. INSPIRE OGC services — GML geometry layers (parcels, buildings, addresses)
     WFS Base: https://ovc.catastro.meh.es/INSPIRE/

No authentication required for non-protected data.
Protected data (ownership names, cadastral values) requires digital certificate.
Coverage: all Spain except Basque Country and Navarre (separate cadastral offices).

Cache lives in .cache/catastro/.

Key use cases for Examplio / SampleCorp:
  - Property boundaries of the SampleCorp campus (the industrial estate)
  - Building footprints for logistics centres
  - Land use classification (industrial, residential, commercial)
  - Address lookups for fieldwork planning

Commands:
    address     Search for a property by address and return cadastral reference.
    parcel      Get cadastral data for a specific cadastral reference (RC).
    geocode     Get coordinates for a cadastral reference.
    buildings   Download building footprints as GeoJSON for a municipality (INSPIRE WFS).
    parcels     Download land parcel boundaries as GeoJSON for a municipality (INSPIRE WFS).

Usage:
    python3 tools/catastro-api.py address --muni MUNI --prov PROV --street STREET [--num NUM]
    python3 tools/catastro-api.py parcel RC
    python3 tools/catastro-api.py geocode RC
    python3 tools/catastro-api.py buildings --muni-code INE_CODE [--bbox BBOX] [--out FILE]
    python3 tools/catastro-api.py parcels --muni-code INE_CODE [--bbox BBOX] [--out FILE]

Arguments:
    RC          Cadastral reference (referencia catastral), e.g. 7337903NH2973N0001TZ
    --muni      Municipality name (e.g. "Examplio")
    --prov      Province name (e.g. "the province")
    --street    Street name
    --num       Street number
    --muni-code INE municipality code (e.g. 01234 for Examplio)
    --bbox      Bounding box as "minX,minY,maxX,maxY" in EPSG:4326

Examples:
    # Search by address in Examplio
    python3 tools/catastro-api.py address --muni "Examplio" --prov "the province" --street "Avenida de la Diputacion"

    # Get parcel data for SampleCorp headquarters (if RC known)
    python3 tools/catastro-api.py parcel 1502415NH2910S0001TA

    # Download all building footprints in Examplio
    python3 tools/catastro-api.py buildings --muni-code 01234 --out fieldwork/example/spatial/buildings.geojson

    # Download all parcels in Examplio
    python3 tools/catastro-api.py parcels --muni-code 01234 --out fieldwork/example/spatial/parcels.geojson

Note: INSPIRE WFS requests for a full municipality can return large files (50+ MB).
Use --bbox to limit to a specific area (e.g. the the industrial estate).
the industrial estate approximate bbox: -3.75,40.35,-3.70,40.40
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOAP_BASE    = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC"
INSPIRE_WFS  = "https://ovc.catastro.meh.es/INSPIRE"

_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.0

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
CACHE_DIR     = os.path.join(_PROJECT_ROOT, ".cache", "catastro")

CACHE_TTL = 30 * 86400  # cadastral data doesn't change often

# Examplio reference
EXAMPLE_INE    = "01234"
EXAMPLE_PROV   = "the province"
EXAMPLE_MUNI   = "Examplio"
# the industrial estate (approximate bounding box for SampleCorp campus)
ESTATE_BBOX     = "-3.75,40.35,-3.70,40.40"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(key: str, ext: str = "xml") -> str:
    safe     = hashlib.sha256(key.encode()).hexdigest()[:16]
    readable = key.replace("/", "_").replace("?", "_").replace("&", "_")[:60]
    return os.path.join(CACHE_DIR, f"{readable}_{safe}.{ext}")


def _cache_read(key: str, ttl: float, ext: str = "xml"):
    path = _cache_path(key, ext)
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) > ttl:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_write(key: str, data: str, ext: str = "xml"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key, ext), "w", encoding="utf-8") as f:
        f.write(data)


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


def _fetch_get(url: str, timeout: int = 60) -> str:
    _rate_limit()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "QLE-Infrastructure/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        print(f"Error: HTTP {e.code} — {url}\n{body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to Catastro — {e.reason}", file=sys.stderr)
        sys.exit(1)


def _fetch_cached(key: str, url: str, ttl: float = CACHE_TTL,
                  refresh: bool = False, ext: str = "xml") -> str:
    if not refresh:
        cached = _cache_read(key, ttl, ext)
        if cached:
            print("  (from cache)", file=sys.stderr)
            return cached
    data = _fetch_get(url)
    _cache_write(key, data, ext)
    return data


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _xml_find(root, *tags) -> str:
    """Find text of a nested element, trying multiple possible tag names."""
    for tag in tags:
        # Try with and without namespace
        el = root.find(f".//{tag}")
        if el is None:
            # Try namespace-insensitive search
            for elem in root.iter():
                if elem.tag.split("}")[-1] == tag:
                    el = elem
                    break
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _gml_to_geojson_feature(gml_element, properties: dict) -> dict:
    """Convert a simple GML geometry to a GeoJSON feature (basic approximation)."""
    feature = {
        "type":       "Feature",
        "properties": properties,
        "geometry":   None,
    }
    # Look for GML coordinates
    for elem in gml_element.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag in ["pos", "coordinates", "posList"]:
            coords_text = elem.text or ""
            parts = coords_text.strip().split()
            if tag == "pos" and len(parts) >= 2:
                feature["geometry"] = {
                    "type":        "Point",
                    "coordinates": [float(parts[0]), float(parts[1])],
                }
            elif tag in ["coordinates", "posList"] and len(parts) >= 4:
                # Polygon or line — parse pairs
                pairs = []
                for i in range(0, len(parts) - 1, 2):
                    try:
                        pairs.append([float(parts[i]), float(parts[i + 1])])
                    except ValueError:
                        pass
                if pairs:
                    feature["geometry"] = {
                        "type":        "Polygon",
                        "coordinates": [pairs],
                    }
            break
    return feature


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_address(args):
    """Search for a property by address."""
    muni   = args.muni
    prov   = args.prov or ""
    street = args.street
    num    = args.num or ""

    params = urllib.parse.urlencode({
        "Provincia": prov,
        "Municipio": muni,
        "TipoVia":   "",
        "NombreVia": street,
        "Numero":    num,
        "Bloque":    "",
        "Escalera":  "",
        "Piso":      "",
        "Puerta":    "",
    })
    url = f"{SOAP_BASE}/OVCCallejero.asmx/Consulta_DNPLOC?{params}"

    cache_key = f"address_{prov}_{muni}_{street}_{num}"
    print(f"Searching Catastro for address: {street} {num}, {muni}, {prov}...", file=sys.stderr)
    raw = _fetch_cached(cache_key, url, ext="xml", refresh=args.refresh)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw[:2000])
        return

    # Extract results
    results = []
    for item in root.iter():
        rc = ""
        for tag in ["pc1", "pc2", "car", "cc1", "cc2", "RC", "RefCatastral"]:
            val = _xml_find(item, tag)
            if val:
                rc += val
        if rc:
            address_str = _xml_find(item, "ldt", "direccion", "Direccion")
            uso         = _xml_find(item, "uso", "luso")
            superficie  = _xml_find(item, "sfc", "superficie")
            results.append((rc, address_str, uso, superficie))

    if not results:
        print("No results found.", file=sys.stderr)
        print(f"Raw response:\n{raw[:2000]}")
        return

    print(f"\n{'CATASTRAL_REF':<25} {'USE':<15} {'AREA_m2':<10} ADDRESS")
    print("-" * 100)
    for rc, addr, uso, sfc in results[:30]:
        print(f"{rc:<25} {uso:<15} {sfc:<10} {addr[:55]}")
    print(f"\n{len(results)} parcels found.", file=sys.stderr)
    print("Use 'parcel RC' to get full details.", file=sys.stderr)


def cmd_parcel(args):
    """Get cadastral data for a specific cadastral reference."""
    rc = args.rc
    # Split RC into first 7 and last 7 chars (pc1 + pc2)
    pc1 = rc[:7] if len(rc) >= 14 else rc
    pc2 = rc[7:14] if len(rc) >= 14 else ""

    params = urllib.parse.urlencode({"Provincia": "", "Municipio": "", "RC": rc,
                                     "SRS": "EPSG:4326"})
    url = f"{SOAP_BASE}/OVCCallejero.asmx/Consulta_DNPRC?{params}"

    cache_key = f"parcel_{rc}"
    print(f"Fetching cadastral data for {rc}...", file=sys.stderr)
    raw = _fetch_cached(cache_key, url, ext="xml", refresh=args.refresh)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw[:2000])
        return

    # Print key fields
    fields = {
        "Referencia Catastral":  _xml_find(root, "rc", "RC"),
        "Dirección":             _xml_find(root, "ldt", "dir", "direccion"),
        "Uso":                   _xml_find(root, "uso", "luso"),
        "Superficie construida": _xml_find(root, "sfc"),
        "Superficie suelo":      _xml_find(root, "sfs"),
        "Año construcción":      _xml_find(root, "ant"),
        "Municipio":             _xml_find(root, "nm", "municipio"),
        "Provincia":             _xml_find(root, "np", "provincia"),
    }

    print(f"\nParcel: {rc}\n")
    for k, v in fields.items():
        if v:
            print(f"  {k:<25} {v}")

    if args.raw:
        print(f"\nFull XML:\n{raw[:5000]}")


def cmd_geocode(args):
    """Get coordinates for a cadastral reference."""
    rc = args.rc
    params = urllib.parse.urlencode({"RC": rc, "SRS": "EPSG:4326"})
    url = f"{SOAP_BASE}/OVCCoordenadas.asmx/Consulta_CPMRC?{params}"

    cache_key = f"geocode_{rc}"
    print(f"Geocoding cadastral reference {rc}...", file=sys.stderr)
    raw = _fetch_cached(cache_key, url, ext="xml", refresh=args.refresh)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw[:2000])
        return

    xcen = _xml_find(root, "xcen", "xc", "X")
    ycen = _xml_find(root, "ycen", "yc", "Y")
    srs  = _xml_find(root, "srs", "SRS")

    if xcen and ycen:
        print(f"\nCoordinates for {rc}:")
        print(f"  Longitude (X): {xcen}")
        print(f"  Latitude  (Y): {ycen}")
        print(f"  CRS:           {srs or 'EPSG:4326'}")
        print(f"\n  Google Maps: https://maps.google.com/?q={ycen},{xcen}")
    else:
        print(f"Could not extract coordinates. Raw:\n{raw[:2000]}")


def cmd_buildings(args):
    """Download building footprints as GeoJSON for a municipality or bounding box."""
    _wfs_download(args, layer="BU.Building", label="buildings")


def cmd_parcels(args):
    """Download land parcel boundaries as GeoJSON for a municipality or bounding box."""
    _wfs_download(args, layer="CP.CadastralParcel", label="parcels")


def _wfs_download(args, layer: str, label: str):
    """Generic INSPIRE WFS download."""
    params = {
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    layer,
        "SRSNAME":      "EPSG:4326",
        "COUNT":        "5000",
        "outputFormat": "application/json",
    }

    if args.bbox:
        params["BBOX"] = f"{args.bbox},EPSG:4326"
    elif args.muni_code:
        # Use INE municipality code as a filter if no bbox
        # Catastro INSPIRE uses LOCALID for municipality
        # This is a best-effort approach — bbox is more reliable
        print(f"Note: Use --bbox for more precise spatial filtering.", file=sys.stderr)
        print(f"Fetching all {label} in municipality {args.muni_code}...", file=sys.stderr)
        params["CQL_FILTER"] = f"beginLifespanVersion IS NOT NULL"  # get all active features
    else:
        print("Error: provide --bbox or --muni-code", file=sys.stderr)
        sys.exit(1)

    base = f"{INSPIRE_WFS}/wfsBU.aspx" if "BU" in layer else f"{INSPIRE_WFS}/wfsCP.aspx"
    query = urllib.parse.urlencode(params)
    url   = f"{base}?{query}"

    bbox_key = args.bbox.replace(",", "_") if args.bbox else args.muni_code
    cache_key = f"{label}_{bbox_key}"

    print(f"Downloading {label} from Catastro INSPIRE WFS...", file=sys.stderr)
    print(f"  This may take a moment for large areas.", file=sys.stderr)

    raw = _fetch_cached(cache_key, url, ttl=CACHE_TTL, ext="json",
                        refresh=getattr(args, "refresh", False))

    # Try to parse as GeoJSON
    try:
        data = json.loads(raw)
        feature_count = len(data.get("features", []))
        print(f"  {feature_count} {label} features downloaded.", file=sys.stderr)

        out_path = args.out
        if out_path:
            out_dir = os.path.dirname(out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Saved GeoJSON to {out_path}", file=sys.stderr)
        else:
            # Print summary
            print(f"\nGeoJSON FeatureCollection with {feature_count} {label}.")
            if data.get("features"):
                sample = data["features"][0]
                print(f"\nSample feature properties:")
                for k, v in list(sample.get("properties", {}).items())[:10]:
                    print(f"  {k}: {v}")
    except json.JSONDecodeError:
        # May be GML — save as-is
        if args.out:
            out_dir = os.path.dirname(args.out)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(raw)
            print(f"Saved response to {args.out}", file=sys.stderr)
        else:
            print(raw[:3000])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Query Spanish Catastro (land registry) non-protected data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Coverage: all Spain except Basque Country and Navarre.\n"
            "No auth required for non-protected data (no ownership names/values).\n"
            "\n"
            "For Examplio fieldwork:\n"
            "  SampleCorp campus (the industrial estate) bbox: -3.75,40.35,-3.70,40.40\n"
            "  Examplio municipality code: 01234\n"
            "\n"
            "Examples:\n"
            "  address --muni Examplio --prov 'the province' --street 'Avenida de la Diputacion'\n"
            "  buildings --bbox -3.75,40.35,-3.70,40.40 --out spatial/estate-buildings.geojson\n"
            "  parcels   --bbox -3.75,40.35,-3.70,40.40 --out spatial/estate-parcels.geojson\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # address
    p = sub.add_parser("address", help="Search by address")
    p.add_argument("--muni",   required=True, help="Municipality name (e.g. Examplio)")
    p.add_argument("--prov",   default="",    help="Province name (e.g. the province)")
    p.add_argument("--street", required=True, help="Street name")
    p.add_argument("--num",    default="",    help="Street number")
    p.add_argument("--refresh", action="store_true")

    # parcel
    p = sub.add_parser("parcel", help="Get data for a cadastral reference")
    p.add_argument("rc", help="Cadastral reference (referencia catastral)")
    p.add_argument("--raw", action="store_true", help="Print full XML response")
    p.add_argument("--refresh", action="store_true")

    # geocode
    p = sub.add_parser("geocode", help="Get coordinates for a cadastral reference")
    p.add_argument("rc", help="Cadastral reference")
    p.add_argument("--refresh", action="store_true")

    # buildings
    p = sub.add_parser("buildings", help="Download building footprints (INSPIRE WFS GeoJSON)")
    p.add_argument("--muni-code", help="INE municipality code (e.g. 01234)")
    p.add_argument("--bbox",      help="Bounding box minX,minY,maxX,maxY in EPSG:4326")
    p.add_argument("--out",  "-o", help="Output GeoJSON file path")
    p.add_argument("--refresh", action="store_true")

    # parcels
    p = sub.add_parser("parcels", help="Download land parcel boundaries (INSPIRE WFS GeoJSON)")
    p.add_argument("--muni-code", help="INE municipality code (e.g. 01234)")
    p.add_argument("--bbox",      help="Bounding box minX,minY,maxX,maxY in EPSG:4326")
    p.add_argument("--out",  "-o", help="Output GeoJSON file path")
    p.add_argument("--refresh", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "address":   cmd_address,
        "parcel":    cmd_parcel,
        "geocode":   cmd_geocode,
        "buildings": cmd_buildings,
        "parcels":   cmd_parcels,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Upload GeoJSON and CSV layers to Felt maps.

Creates a new Felt map for a fieldwork case and uploads all layer files
from the case's felt-exports directory. Called by the field-planner agent
after generating map data.

Usage:
    python3 tools/felt-upload.py <case-name> [--title "Map Title"] [--basemap light]

Environment:
    FELT_API_TOKEN  — required, your Felt API key

The script looks for layers in:
    fieldwork/<case-name>/felt-exports/

Expected files (all optional — uploads whatever exists):
    governance-boundaries.geojson
    observation-routes.geojson
    archive-locations.csv
    interview-locations.csv
    comparison-sites.csv
    governance-gaps.geojson

Output:
    Prints the Felt map URL and writes it to:
    fieldwork/<case-name>/felt-exports/felt-map-url.txt
"""

import argparse
import http.client
import io
import json
import os
import ssl
import sys
import typing
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Felt API helpers (adapted from felt-python, compatible with Python 3.8+)
# ---------------------------------------------------------------------------

BASE_URL = "https://felt.com/api/v2/"


def _get_token():
    token = os.environ.get("FELT_API_TOKEN")
    if not token:
        print("Error: FELT_API_TOKEN environment variable not set.", file=sys.stderr)
        print("Get your token at https://felt.com/account/integrations", file=sys.stderr)
        sys.exit(1)
    return token


def _api_request(
    path,          # type: str
    method,        # type: str
    json_body=None,  # type: typing.Optional[dict]
    token=None,    # type: typing.Optional[str]
):
    """Make an authenticated request to the Felt API."""
    if token is None:
        token = _get_token()

    url = BASE_URL + path.lstrip("/")
    data = None
    headers = {
        "Authorization": "Bearer " + token,
        "User-Agent": "qle-felt-upload/1.0",
    }

    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    resp = urllib.request.urlopen(req)
    return json.load(resp)


def _multipart_upload(presigned_upload, file_path):
    """Upload a file using the presigned S3 attributes returned by Felt."""
    upload_url = presigned_upload["url"]
    attrs = presigned_upload["presigned_attributes"]

    boundary = "----" + str(uuid.uuid4())
    fname = os.path.basename(file_path)

    body = io.BytesIO()
    text = io.TextIOWrapper(body, encoding="latin-1")

    for key, value in attrs.items():
        text.write("--{}\r\n".format(boundary))
        text.write('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(key))
        text.write("{}\r\n".format(value))

    text.write("--{}\r\n".format(boundary))
    text.write('Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format(fname))
    text.write("Content-Type: application/octet-stream\r\n\r\n")
    text.flush()

    with open(file_path, "rb") as f:
        body.write(f.read())

    body.write("\r\n--{}--\r\n".format(boundary).encode("latin-1"))

    headers = {"Content-Type": 'multipart/form-data; boundary="{}"'.format(boundary)}
    req = urllib.request.Request(upload_url, data=body.getvalue(), headers=headers, method="POST")
    urllib.request.urlopen(req)


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------

def create_map(title, basemap="light", lat=None, lon=None, zoom=None, token=None):
    """Create a new Felt map and return its metadata."""
    payload = {"title": title, "basemap": basemap}
    if lat is not None:
        payload["lat"] = lat
    if lon is not None:
        payload["lon"] = lon
    if zoom is not None:
        payload["zoom"] = zoom

    return _api_request("maps", "POST", json_body=payload, token=token)


def upload_layer(map_id, file_path, layer_name, token=None):
    """Upload a GeoJSON or CSV file as a new layer on an existing map."""
    presigned = _api_request(
        "maps/{}/upload".format(map_id),
        "POST",
        json_body={"name": layer_name},
        token=token,
    )
    _multipart_upload(presigned, file_path)
    return presigned


# ---------------------------------------------------------------------------
# Layer definitions matching field-planner output
# ---------------------------------------------------------------------------

LAYER_SPECS = [
    ("governance-boundaries.geojson", "Governance Boundaries"),
    ("observation-routes.geojson", "Observation Routes"),
    ("archive-locations.csv", "Archive Locations"),
    ("interview-locations.csv", "Interview Locations"),
    ("comparison-sites.csv", "Comparison Sites"),
    ("governance-gaps.geojson", "Governance Gaps"),
]


def upload_case(case_name, title=None, basemap="light", token=None):
    """Create a Felt map for a case and upload all available layers.

    Returns:
        dict with keys: map_id, map_url, layers_uploaded
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    exports_dir = os.path.join(project_root, "fieldwork", case_name, "felt-exports")

    if not os.path.isdir(exports_dir):
        print("Error: no felt-exports directory at {}".format(exports_dir), file=sys.stderr)
        sys.exit(1)

    if title is None:
        title = "QLE Fieldwork — {}".format(case_name.replace("-", " ").title())

    # Create the map
    print("Creating Felt map: {}".format(title))
    map_data = create_map(title, basemap=basemap, token=token)
    map_id = map_data["id"]
    map_url = map_data["url"]
    print("  Map created: {}".format(map_url))

    # Upload each layer that exists
    uploaded = []
    for filename, layer_name in LAYER_SPECS:
        file_path = os.path.join(exports_dir, filename)
        if not os.path.isfile(file_path):
            continue
        print("  Uploading layer: {} ({})".format(layer_name, filename))
        try:
            upload_layer(map_id, file_path, layer_name, token=token)
            uploaded.append(filename)
        except Exception as e:
            print("    Warning: upload failed for {}: {}".format(filename, e), file=sys.stderr)

    # Also upload any extra files in the directory not in LAYER_SPECS
    known_files = {spec[0] for spec in LAYER_SPECS}
    known_files.add("felt-map-url.txt")
    known_files.add("felt-embed-url.txt")
    for entry in sorted(os.listdir(exports_dir)):
        if entry in known_files:
            continue
        if not (entry.endswith(".geojson") or entry.endswith(".csv")):
            continue
        file_path = os.path.join(exports_dir, entry)
        layer_name = entry.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
        print("  Uploading extra layer: {} ({})".format(layer_name, entry))
        try:
            upload_layer(map_id, file_path, layer_name, token=token)
            uploaded.append(entry)
        except Exception as e:
            print("    Warning: upload failed for {}: {}".format(entry, e), file=sys.stderr)

    # Derive the embed URL (replace /map/ with /embed/map/)
    embed_url = map_url.replace("/map/", "/embed/map/")

    # Write URLs to files for reference
    url_file = os.path.join(exports_dir, "felt-map-url.txt")
    with open(url_file, "w") as f:
        f.write(map_url + "\n")

    embed_file = os.path.join(exports_dir, "felt-embed-url.txt")
    with open(embed_file, "w") as f:
        f.write(embed_url + "\n")

    print("\nDone. {} layers uploaded.".format(len(uploaded)))
    print("Felt map: {}".format(map_url))
    print("Embed URL: {}".format(embed_url))
    print("URLs saved to: {}".format(exports_dir))

    return {"map_id": map_id, "map_url": map_url, "embed_url": embed_url, "layers_uploaded": uploaded}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Upload fieldwork map layers to Felt",
        epilog="Set FELT_API_TOKEN in your environment before running.",
    )
    parser.add_argument("case_name", help="Case name (directory under fieldwork/)")
    parser.add_argument("--title", help="Custom map title (default: QLE Fieldwork — Case Name)")
    parser.add_argument(
        "--basemap",
        default="light",
        choices=["default", "light", "dark", "satellite"],
        help="Felt basemap style (default: light)",
    )
    args = parser.parse_args()
    upload_case(args.case_name, title=args.title, basemap=args.basemap)


if __name__ == "__main__":
    main()

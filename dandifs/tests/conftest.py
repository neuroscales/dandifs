"""Shared fixtures and an offline fake DANDI archive for tests."""
import fnmatch
import json
import re
from typing import List

import pytest
from aioresponses import CallbackResult, aioresponses

API = "https://api.dandiarchive.org/api"
S3 = "https://dandiarchive.s3.amazonaws.com"

DANDISET = "000026"
VERSION = "0.210831.2033"
ZARR_ID = "11111111-2222-3333-4444-555555555555"

# path -> asset definition
_ASSETS = {
    "sub-01/anat/scan.json": {
        "asset_id": "aaaa-json",
        "size": 12,
        "blob": "blob-json",
    },
    "sub-01/anat/scan.nwb": {
        "asset_id": "aaaa-nwb",
        "size": 2048,
        "blob": "blob-nwb",
    },
    "sub-02/anat/scan.json": {
        "asset_id": "bbbb-json",
        "size": 34,
        "blob": "blob-json2",
    },
    "data/image.zarr": {
        "asset_id": "cccc-zarr",
        "size": 4096,
        "zarr": ZARR_ID,
    },
}

# Files inside the zarr store (keys relative to the zarr root).
_ZARR_FILES = {
    ".zgroup": b'{"zarr_format":2}',
    ".zattrs": b"{}",
    "0/.zarray": b'{"chunks":[1]}',
    "0/0": b"\x00\x01\x02\x03",
    "0/1": b"\x04\x05\x06\x07",
}

# byte payloads served from S3 for blob assets
_BLOB_BYTES = {
    "aaaa-json": b'{"hello": 1}',
    "aaaa-nwb": b"\x00" * 2048,
    "bbbb-json": b'{"world": 2}',
    "cccc-zarr": b"",
}


def _content_urls(asset: dict) -> List[str]:
    aid = asset["asset_id"]
    if "zarr" in asset:
        return [
            "{}/assets/{}/download/".format(API, aid),
            "{}/zarr/{}/".format(S3, asset["zarr"]),
        ]
    return [
        "{}/assets/{}/download/".format(API, aid),
        "{}/blobs/{}".format(S3, asset["blob"]),
    ]


def _asset_record(path: str, metadata: bool) -> dict:
    a = _ASSETS[path]
    rec = {
        "asset_id": a["asset_id"],
        "path": path,
        "size": a["size"],
        "created": "2021-01-01T00:00:00Z",
        "modified": "2021-01-02T00:00:00Z",
    }
    if "zarr" in a:
        rec["zarr"] = a["zarr"]
    else:
        rec["blob"] = a["blob"]
    if metadata:
        rec["metadata"] = {"contentUrl": _content_urls(a)}
    return rec


def _assets_callback(url, **kwargs):
    q = url.query
    path = q.get("path")
    glob = q.get("glob")
    metadata = q.get("metadata") == "true"
    results = []
    for p in _ASSETS:
        if path is not None and not p.startswith(path):
            continue
        if glob is not None and not fnmatch.fnmatch(p, glob):
            continue
        results.append(_asset_record(p, metadata))
    return CallbackResult(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results, "next": None}),
    )


def _paths_callback(url, **kwargs):
    q = url.query
    prefix = q.get("path_prefix") or ""
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"
    children = {}  # name -> (is_file, path, size)
    for p, a in _ASSETS.items():
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix):]
        seg = rest.split("/", 1)[0]
        full = prefix + seg
        is_file = rest == seg
        if full not in children or is_file:
            children[full] = (is_file, p, a["size"])
    results = []
    for full, (is_file, p, size) in children.items():
        if is_file:
            results.append({
                "path": full,
                "total_size": size,
                "asset": {
                    "asset_id": _ASSETS[p]["asset_id"],
                    "path": p,
                    "size": size,
                    "created": "2021-01-01T00:00:00Z",
                    "modified": "2021-01-02T00:00:00Z",
                },
            })
        else:
            results.append({"path": full, "total_size": size, "asset": None})
    return CallbackResult(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results, "next": None}),
    )


def _zarr_files_callback(url, **kwargs):
    q = url.query
    prefix = q.get("prefix") or ""
    results = []
    for key, data in _ZARR_FILES.items():
        if key.startswith(prefix):
            results.append({"Key": key, "Size": len(data)})
    return CallbackResult(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results, "next": None}),
    )


def register_archive(m: aioresponses, embargoed: bool = False) -> None:
    """Register the fake archive's endpoints on an aioresponses mock."""
    # dandiset record (default version resolution)
    m.get(
        re.compile(re.escape(API + "/dandisets/" + DANDISET + "/") + r"$"),
        payload={
            "identifier": DANDISET,
            "most_recent_published_version": {"version": VERSION},
            "draft_version": {"version": "draft"},
        },
        repeat=True,
    )
    # assets listing
    m.get(
        re.compile(r".*/versions/[^/]+/assets/(\?.*)?$"),
        callback=_assets_callback,
        repeat=True,
    )
    # assets paths listing
    m.get(
        re.compile(r".*/versions/[^/]+/assets/paths/(\?.*)?$"),
        callback=_paths_callback,
        repeat=True,
    )
    # zarr files listing
    m.get(
        re.compile(r".*/zarr/[^/]+/files/(\?.*)?$"),
        callback=_zarr_files_callback,
        repeat=True,
    )
    # per-blob byte URLs (exact) on S3
    for path, a in _ASSETS.items():
        if "blob" in a:
            m.get(
                "{}/blobs/{}".format(S3, a["blob"]),
                body=_BLOB_BYTES[a["asset_id"]],
                repeat=True,
            )
    # zarr chunk bytes on S3
    for key, data in _ZARR_FILES.items():
        m.get(
            "{}/zarr/{}/{}".format(S3, ZARR_ID, key),
            body=data,
            repeat=True,
        )


@pytest.fixture
def mock_archive():
    """Yield an aioresponses mock pre-loaded with the fake archive."""
    with aioresponses() as m:
        register_archive(m)
        yield m

"""Tests for URL parsing (offline, no I/O)."""

import pytest

from dandifs._exceptions import UnknownURLError
from dandifs._parser import parse_dandi_url


def test_dandi_scheme_basic():
    p = parse_dandi_url("dandi://dandi/000026")
    assert p.instance.name == "dandi"
    assert p.dandiset_id == "000026"
    assert p.version_id is None
    assert p.path is None


def test_dandi_scheme_version():
    p = parse_dandi_url("dandi://dandi/000026@draft")
    assert p.version_id == "draft"
    p = parse_dandi_url("dandi://dandi/000026@0.210831.2033")
    assert p.version_id == "0.210831.2033"


def test_dandi_scheme_path():
    p = parse_dandi_url("dandi://dandi/000026/sub-01/anat/scan.json")
    assert p.path == "sub-01/anat/scan.json"


def test_dandi_scheme_zarr_subpath():
    p = parse_dandi_url("dandi://dandi/000026@draft/data/image.zarr/0/0/0")
    assert p.dandiset_id == "000026"
    assert p.version_id == "draft"
    assert p.path == "data/image.zarr/0/0/0"


def test_dandi_colon_form():
    p = parse_dandi_url("DANDI:000026")
    assert p.instance.name == "dandi"
    assert p.dandiset_id == "000026"
    p = parse_dandi_url("DANDI:000026/0.210831.2033")
    assert p.version_id == "0.210831.2033"


def test_other_instance():
    p = parse_dandi_url("dandi://ember/000026/a/b.zarr/c")
    assert p.instance.name == "ember"
    assert p.path == "a/b.zarr/c"


def test_api_asset_id_url():
    p = parse_dandi_url(
        "https://api.dandiarchive.org/api/dandisets/000026/"
        "versions/draft/assets/abc-123/download/"
    )
    assert p.asset_id == "abc-123"
    assert p.version_id == "draft"


def test_api_path_query():
    p = parse_dandi_url(
        "https://api.dandiarchive.org/api/dandisets/000026/"
        "versions/draft/assets/?path=sub-01/"
    )
    assert p.path == "sub-01/"


def test_api_glob_query():
    p = parse_dandi_url(
        "https://api.dandiarchive.org/api/dandisets/000026/"
        "versions/draft/assets/?glob=%2A%2A%2F%2A.json"
    )
    assert p.glob == "**/*.json"


def test_gui_files_location():
    p = parse_dandi_url(
        "https://gui.dandiarchive.org/dandiset/000026/draft/"
        "files?location=sub-01%2Fscan.nwb"
    )
    assert p.dandiset_id == "000026"
    assert p.version_id == "draft"
    assert p.path == "sub-01/scan.nwb"


def test_glob_flag_promotes_path():
    p = parse_dandi_url("dandi://dandi/000026/**/anat/*.json", glob=True)
    assert p.glob == "**/anat/*.json"
    assert p.path is None


def test_unknown_url():
    with pytest.raises(UnknownURLError):
        parse_dandi_url("ftp://example.org/whatever")


def test_unknown_instance():
    with pytest.raises(UnknownURLError):
        parse_dandi_url("dandi://not-a-real-instance/000026")

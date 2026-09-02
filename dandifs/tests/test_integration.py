"""
Network-gated integration tests against the real public DANDI archive.

These are skipped unless ``DANDIFS_TEST_NETWORK=1`` is set in the environment,
so the default test run stays fully offline.
"""
import json
import os

import pytest

from dandifs import DandiFileSystem

pytestmark = pytest.mark.skipif(
    os.environ.get("DANDIFS_TEST_NETWORK") != "1",
    reason="set DANDIFS_TEST_NETWORK=1 to run network integration tests",
)

# A small, stable, public dandiset.
DANDISET = "000026"


def test_ls_public_dandiset():
    fs = DandiFileSystem(DANDISET, skip_instance_cache=True)
    top = fs.ls("", detail=False)
    assert top, "expected some top-level entries"


def test_open_public_json():
    # A known JSON asset in dandiset 000026.
    url = (
        "dandi://dandi/000026/rawdata/sub-I38/ses-MRI/anat/"
        "sub-I38_ses-MRI-echo-4_flip-4_VFA.json"
    )
    with DandiFileSystem.for_url(url, skip_instance_cache=True).open(url) as f:
        data = json.load(f)
    assert isinstance(data, dict)


def test_glob_public():
    fs = DandiFileSystem(DANDISET, skip_instance_cache=True)
    matches = fs.glob("**/anat/*.json")
    assert isinstance(matches, list)

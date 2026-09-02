"""Tests for DandiFileSystem against the mocked archive."""
import pytest

from dandifs import DandiFileSystem
from dandifs.tests.conftest import DANDISET, VERSION, ZARR_ID

ROOT = "dandi://dandi/{}@{}".format(DANDISET, VERSION)


async def _close(fs):
    if fs._session is not None:
        await fs._session.close()
    if fs._http._session is not None:
        await fs._http._session.close()


def _afs():
    return DandiFileSystem(DANDISET, asynchronous=True, skip_instance_cache=True)


async def test_ls_root(mock_archive):
    fs = _afs()
    try:
        names = await fs._ls("", detail=False)
    finally:
        await _close(fs)
    assert ROOT + "/sub-01" in names
    assert ROOT + "/sub-02" in names
    assert ROOT + "/data" in names


async def test_ls_directory(mock_archive):
    fs = _afs()
    try:
        entries = await fs._ls("sub-01/anat", detail=True)
    finally:
        await _close(fs)
    names = {e["name"] for e in entries}
    assert ROOT + "/sub-01/anat/scan.json" in names
    assert ROOT + "/sub-01/anat/scan.nwb" in names
    for e in entries:
        assert e["type"] == "file"


async def test_info_file(mock_archive):
    fs = _afs()
    try:
        info = await fs._info("sub-01/anat/scan.json")
    finally:
        await _close(fs)
    assert info["type"] == "file"
    assert info["size"] == 12
    assert info["name"] == ROOT + "/sub-01/anat/scan.json"


async def test_info_directory(mock_archive):
    fs = _afs()
    try:
        info = await fs._info("sub-01")
    finally:
        await _close(fs)
    assert info["type"] == "directory"


async def test_exists(mock_archive):
    fs = _afs()
    try:
        assert await fs._exists("sub-01/anat/scan.json") is True
        assert await fs._exists("sub-01/anat/missing.json") is False
        assert await fs._exists("sub-01") is True
    finally:
        await _close(fs)


async def test_glob(mock_archive):
    fs = _afs()
    try:
        names = await fs._glob("**/*.json")
    finally:
        await _close(fs)
    assert ROOT + "/sub-01/anat/scan.json" in names
    assert ROOT + "/sub-02/anat/scan.json" in names
    assert ROOT + "/sub-01/anat/scan.nwb" not in names


async def test_cat_file_blob(mock_archive):
    fs = _afs()
    try:
        data = await fs._cat_file("sub-01/anat/scan.json")
    finally:
        await _close(fs)
    assert data == b'{"hello": 1}'


# ----------------------------- Zarr subpaths -----------------------------


async def test_zarr_root_is_directory(mock_archive):
    fs = _afs()
    try:
        info = await fs._info("data/image.zarr")
    finally:
        await _close(fs)
    assert info["type"] == "directory"


async def test_ls_inside_zarr(mock_archive):
    fs = _afs()
    try:
        entries = await fs._ls("data/image.zarr", detail=True)
    finally:
        await _close(fs)
    names = {e["name"] for e in entries}
    assert ROOT + "/data/image.zarr/.zgroup" in names
    assert ROOT + "/data/image.zarr/.zattrs" in names
    # "0" is a directory (contains .zarray and chunks)
    assert ROOT + "/data/image.zarr/0" in names
    by_name = {e["name"]: e for e in entries}
    assert by_name[ROOT + "/data/image.zarr/0"]["type"] == "directory"


async def test_info_file_inside_zarr(mock_archive):
    fs = _afs()
    try:
        info = await fs._info("data/image.zarr/0/0")
    finally:
        await _close(fs)
    assert info["type"] == "file"
    assert info["size"] == 4


async def test_cat_file_inside_zarr(mock_archive):
    fs = _afs()
    try:
        data = await fs._cat_file("data/image.zarr/0/0")
    finally:
        await _close(fs)
    assert data == b"\x00\x01\x02\x03"


async def test_exists_inside_zarr(mock_archive):
    fs = _afs()
    try:
        assert await fs._exists("data/image.zarr/0/0") is True
        assert await fs._exists("data/image.zarr/0") is True  # directory
        assert await fs._exists("data/image.zarr/9/9") is False
    finally:
        await _close(fs)


async def test_zarr_key_directory_info(mock_archive):
    fs = _afs()
    try:
        info = await fs._info("data/image.zarr/0")
    finally:
        await _close(fs)
    assert info["type"] == "directory"


async def test_full_url_paths(mock_archive):
    fs = DandiFileSystem(asynchronous=True, skip_instance_cache=True)
    try:
        data = await fs._cat_file(
            "dandi://dandi/000026/sub-01/anat/scan.json"
        )
    finally:
        await _close(fs)
    assert data == b'{"hello": 1}'


async def test_unbound_relative_path_errors(mock_archive):
    fs = DandiFileSystem(asynchronous=True, skip_instance_cache=True)
    try:
        with pytest.raises(ValueError):
            await fs._info("sub-01/anat/scan.json")
    finally:
        await _close(fs)


def test_s3_url_property(mock_archive):
    # sync path: resolve the direct byte URL
    fs = DandiFileSystem(DANDISET, skip_instance_cache=True)
    url = fs.s3_url("data/image.zarr/0/0")
    assert url.endswith("/zarr/{}/0/0".format(ZARR_ID))


def test_sync_cat(mock_archive):
    fs = DandiFileSystem(DANDISET, skip_instance_cache=True)
    data = fs.cat_file("sub-01/anat/scan.json")
    assert data == b'{"hello": 1}'


def test_sync_open(mock_archive):
    fs = DandiFileSystem(DANDISET, skip_instance_cache=True)
    with fs.open("sub-01/anat/scan.json", "rb") as f:
        data = f.read()
    assert data == b'{"hello": 1}'

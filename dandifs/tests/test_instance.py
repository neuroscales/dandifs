"""Tests for the instance registry and (mocked) discovery."""
import aiohttp
import pytest
from aioresponses import aioresponses

from dandifs._instance import (
    discover_instance,
    get_instance,
    known_instances,
    needs_discovery,
)


def test_known_names():
    for name in ("dandi", "dandi-staging", "ember", "ember-sandbox"):
        inst = get_instance(name)
        assert inst.name == name
        assert inst.api.startswith("http")


def test_linc_removed():
    assert "linc" not in known_instances
    assert "linc-staging" not in known_instances


def test_get_instance_by_known_url():
    inst = get_instance("https://api.dandiarchive.org/api")
    assert inst.name == "dandi"


def test_get_instance_by_gui_url():
    inst = get_instance("https://dandiarchive.org")
    assert inst.name == "dandi"


def test_generic_instance_from_unknown_url():
    inst = get_instance("https://api.private.example.org/api")
    assert inst.name == "api.private.example.org"
    assert inst.api == "https://api.private.example.org/api"
    assert needs_discovery(inst)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        get_instance("nope-not-real")


async def test_discovery_info_endpoint():
    url = "https://dandi.example.org"
    payload = {
        "version": "1.0",
        "services": {
            "api": {"url": "https://api.example.org/api"},
            "webui": {"url": "https://gui.example.org"},
        },
    }
    with aioresponses() as m:
        m.get(url + "/info/", payload=payload)
        async with aiohttp.ClientSession() as session:
            inst = await discover_instance(session, url)
    assert inst.api == "https://api.example.org/api"
    assert inst.gui == "https://gui.example.org"
    assert inst.name == "api.example.org"


async def test_discovery_fallback_when_unreachable():
    url = "https://broken.example.org"
    with aioresponses() as m:
        m.get(url + "/info/", status=404)
        m.get(url + "/server-info", status=404)
        m.get(url + "/api/info/", status=404)
        async with aiohttp.ClientSession() as session:
            inst = await discover_instance(session, url)
    assert inst.api == url

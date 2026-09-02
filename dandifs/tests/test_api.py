"""Tests for the async REST client, including lazy auth-on-401."""
import aiohttp
from aioresponses import aioresponses

from dandifs._api import DandiClient

API = "https://api.dandiarchive.org/api"


async def test_public_request_no_auth():
    client = DandiClient(API, "dandi", use_keyring=False)
    with aioresponses() as m:
        m.get(API + "/dandisets/000026/", payload={"identifier": "000026"})
        async with aiohttp.ClientSession() as s:
            rec = await client.get_dandiset(s, "000026")
    assert rec["identifier"] == "000026"
    assert client._token is None


async def test_lazy_auth_on_401(monkeypatch):
    monkeypatch.setenv("DANDI_API_KEY", "secret-token")
    client = DandiClient(API, "dandi", use_keyring=False)
    with aioresponses() as m:
        # First response: unauthorized; second (after auth): success.
        m.get(API + "/dandisets/000026/", status=401)
        m.get(API + "/dandisets/000026/", payload={"identifier": "000026"})
        async with aiohttp.ClientSession() as s:
            rec = await client.get_dandiset(s, "000026")
    assert rec["identifier"] == "000026"
    assert client._token == "secret-token"
    # The retried request carried the Authorization header.
    requests = [
        req
        for key, req in m.requests.items()
        for req in req
    ]
    auth_headers = [
        r.kwargs.get("headers", {}).get("Authorization") for r in requests
    ]
    assert "token secret-token" in auth_headers


async def test_no_auth_retry_without_token(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    client = DandiClient(API, "dandi", use_keyring=False)
    with aioresponses() as m:
        m.get(API + "/dandisets/000026/", status=401)
        async with aiohttp.ClientSession() as s:
            try:
                await client.get_dandiset(s, "000026")
            except Exception as exc:  # noqa: BLE001
                assert "401" in str(exc)
            else:
                raise AssertionError("expected an HTTP error")
    assert client._auth_tried is True

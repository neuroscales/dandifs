# NOTICE
#   This file was inspired by [dandi-cli] dandi/dandiapi.py, which is
#   distributed under the Apache 2.0 license, but has been rewritten to be
#   async (aiohttp) and dependency-light.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""
Internal async REST client for DANDI API servers.

The client is deliberately stateless with respect to the aiohttp session: the
owning filesystem creates and manages the session (on the fsspec event loop)
and passes it into every call. The client only holds the API base URL and the
(lazily resolved) authentication token.
"""
import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional

from ._consts import DRAFT, REQUEST_RETRIES, RETRY_STATUSES
from ._exceptions import DandiHTTPError, FailedToConnectError, HTTP404Error
from ._keyring import resolve_token
from ._utils import USER_AGENT, clean_params, get_logger, joinurl

LOG = get_logger("api")


def _decode(body: bytes) -> str:
    return body.decode("utf-8", "replace")


class DandiClient:
    """An async client for a single DANDI API server."""

    def __init__(
        self,
        api_url: str,
        instance_name: Optional[str] = None,
        token: Optional[str] = None,
        use_keyring: bool = True,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.instance_name = instance_name
        self._token = token
        self._auth_tried = False
        self.use_keyring = use_keyring

    # -- low-level -----------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = "token {}".format(self._token)
        return headers

    async def request(
        self,
        session: Any,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_resp: bool = True,
    ) -> Any:
        """
        Perform an HTTP request, retrying transient failures and resolving
        authentication lazily (only after a 401).
        """
        url = joinurl(self.api_url, path)
        qparams = clean_params(params)
        for attempt in range(REQUEST_RETRIES):
            LOG.debug("%s %s", method.upper(), url)
            async with session.request(
                method, url, params=qparams, headers=self._headers()
            ) as resp:
                status = resp.status
                if status == 401 and not self._auth_tried:
                    self._auth_tried = True
                    token = resolve_token(
                        self.instance_name, use_keyring=self.use_keyring
                    )
                    if token and token != self._token:
                        LOG.debug("Retrying %s with resolved token", url)
                        self._token = token
                        continue
                if status in RETRY_STATUSES:
                    delay = min(0.5 * (2 ** attempt), 10.0)
                    LOG.debug("Status %d for %s; retrying in %.1fs", status, url, delay)
                    await asyncio.sleep(delay)
                    continue
                body = await resp.read()
                if status == 404:
                    raise HTTP404Error(url, _decode(body))
                if status >= 400:
                    raise DandiHTTPError(status, url, _decode(body))
                if not json_resp:
                    return body
                text = _decode(body).strip()
                return json.loads(text) if text else None
        raise FailedToConnectError(
            "Request to {} failed after {} attempts".format(
                url, REQUEST_RETRIES
            )
        )

    async def get(
        self, session: Any, path: str, params: Optional[dict] = None, **kw: Any
    ) -> Any:
        """Convenience GET wrapper around :meth:`request`."""
        return await self.request(session, "GET", path, params=params, **kw)

    async def paginate(
        self, session: Any, path: str, params: Optional[dict] = None
    ) -> AsyncIterator[dict]:
        """Yield items across all pages of a paginated endpoint."""
        page = await self.get(session, path, params=params)
        while page is not None:
            for item in page.get("results", []):
                yield item
            nxt = page.get("next")
            if not nxt:
                break
            page = await self.get(session, nxt)

    # -- DANDI endpoints ----------------------------------------------

    @staticmethod
    def _version_path(dandiset_id: str, version_id: str) -> str:
        return "/dandisets/{}/versions/{}".format(dandiset_id, version_id)

    async def get_dandiset(self, session: Any, dandiset_id: str) -> dict:
        """Return the dandiset record."""
        return await self.get(
            session, "/dandisets/{}/".format(dandiset_id)
        )

    async def resolve_version(
        self, session: Any, dandiset_id: str, version_id: Optional[str]
    ) -> str:
        """Return ``version_id`` or the dandiset's default (published/draft)."""
        if version_id:
            return version_id
        record = await self.get_dandiset(session, dandiset_id)
        published = record.get("most_recent_published_version")
        if published:
            return published["version"]
        draft = record.get("draft_version")
        if draft:
            return draft["version"]
        return DRAFT

    async def assets(
        self,
        session: Any,
        dandiset_id: str,
        version_id: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        order: Optional[str] = None,
        metadata: bool = False,
    ) -> AsyncIterator[dict]:
        """Iterate over assets, optionally filtered by path prefix or glob."""
        params: Dict[str, Any] = {}
        if path is not None:
            params["path"] = path
        if glob is not None:
            params["glob"] = glob
        if order is not None:
            params["order"] = order
        if metadata:
            params["metadata"] = True
        endpoint = self._version_path(dandiset_id, version_id) + "/assets/"
        async for asset in self.paginate(session, endpoint, params):
            yield asset

    async def asset_with_path(
        self,
        session: Any,
        dandiset_id: str,
        version_id: str,
        path: str,
        metadata: bool = True,
    ) -> Optional[dict]:
        """Return the asset whose path equals ``path`` exactly, or ``None``."""
        async for asset in self.assets(
            session, dandiset_id, version_id, path=path, metadata=metadata
        ):
            if asset.get("path") == path:
                return asset
        return None

    async def asset_paths(
        self,
        session: Any,
        dandiset_id: str,
        version_id: str,
        path_prefix: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Iterate over the immediate children (folders/files) of a prefix."""
        params: Dict[str, Any] = {}
        if path_prefix is not None:
            params["path_prefix"] = path_prefix
        endpoint = self._version_path(dandiset_id, version_id) + "/assets/paths/"
        async for entry in self.paginate(session, endpoint, params):
            yield entry

    async def get_asset(
        self,
        session: Any,
        dandiset_id: str,
        version_id: str,
        asset_id: str,
        info: bool = False,
    ) -> dict:
        """Return an asset record (or its ``/info/`` metadata)."""
        suffix = "/info/" if info else "/"
        endpoint = self._version_path(dandiset_id, version_id) + (
            "/assets/{}{}".format(asset_id, suffix)
        )
        return await self.get(session, endpoint)

    async def zarr_files(
        self, session: Any, zarr_id: str, prefix: Optional[str] = None
    ) -> AsyncIterator[dict]:
        """Iterate over entries in a Zarr archive's file listing."""
        params: Dict[str, Any] = {}
        if prefix is not None:
            params["prefix"] = prefix
        async for entry in self.paginate(
            session, "/zarr/{}/files/".format(zarr_id), params
        ):
            yield entry

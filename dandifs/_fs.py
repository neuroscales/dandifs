"""
An :mod:`fsspec` filesystem for the DANDI archive.

The filesystem is async-first: it subclasses :class:`fsspec.asyn.AsyncFileSystem`
and implements the ``_``-prefixed coroutines (``_ls``, ``_info``, ``_cat_file``,
``_exists``, ``_glob``); fsspec generates the synchronous API (``ls``, ``info``,
``cat_file``, ``exists``, ``glob``, ``open`` ...) from them. Byte I/O is
delegated to fsspec's :class:`~fsspec.implementations.http.HTTPFileSystem`,
which shares the same event loop.
"""
import re
import weakref
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
from fsspec import register_implementation
from fsspec.asyn import AsyncFileSystem, sync
from fsspec.exceptions import FSTimeoutError
from fsspec.implementations.http import HTTPFileSystem
from fsspec.registry import known_implementations
from fsspec.utils import stringify_path
from typing_extensions import Self

from ._api import DandiClient
from ._consts import ZARR_EXTENSIONS
from ._exceptions import HTTP404Error
from ._instance import (
    DandiInstance,
    discover_instance,
    get_instance,
    needs_discovery,
)
from ._parser import ParsedDandiURL, parse_dandi_url
from ._utils import get_logger

LOG = get_logger("fs")

_SCHEME_RE = re.compile(r"(?i)^(dandi://|DANDI:|https?://)")


def _content_urls(asset: dict) -> List[str]:
    metadata = asset.get("metadata") or {}
    urls = metadata.get("contentUrl") or []
    return [u for u in urls if isinstance(u, str)]


def _s3_url(asset: dict) -> Optional[str]:
    """The content URL that streams bytes directly (not the /download/ one)."""
    for url in _content_urls(asset):
        if not url.rstrip("/").endswith("/download"):
            return url
    return None


def _download_url(asset: dict) -> Optional[str]:
    for url in _content_urls(asset):
        if url.rstrip("/").endswith("/download"):
            return url
    return None


def _is_zarr(asset: dict) -> bool:
    if asset.get("zarr"):
        return True
    return str(asset.get("path", "")).endswith(ZARR_EXTENSIONS)


class _Node:
    """Classification of a resolved location within a dandiset."""

    __slots__ = (
        "kind",
        "path",
        "url",
        "size",
        "created",
        "modified",
        "asset_id",
        "zarr_id",
        "zarr_root",
        "zarr_key",
    )

    def __init__(
        self,
        kind: str,
        path: str,
        url: Optional[str] = None,
        size: Optional[int] = None,
        created: Optional[str] = None,
        modified: Optional[str] = None,
        asset_id: Optional[str] = None,
        zarr_id: Optional[str] = None,
        zarr_root: Optional[str] = None,
        zarr_key: Optional[str] = None,
    ) -> None:
        self.kind = kind  # 'file' | 'directory' | 'zarr' | 'missing'
        self.path = path
        self.url = url
        self.size = size
        self.created = created
        self.modified = modified
        self.asset_id = asset_id
        self.zarr_id = zarr_id
        self.zarr_root = zarr_root
        self.zarr_key = zarr_key


class DandiFileSystem(AsyncFileSystem):
    """
    A filesystem that browses a remote dandiset.

    Examples
    --------
    Open a remote file via the registered ``dandi://`` protocol::

        import fsspec, json
        with fsspec.open(
            "dandi://dandi/000026/rawdata/sub-I38/ses-MRI/anat/"
            "sub-I38_ses-MRI-echo-4_flip-4_VFA.json"
        ) as f:
            info = json.load(f)

    Bind a filesystem to a dandiset and browse it::

        from dandifs import DandiFileSystem
        fs = DandiFileSystem("000026")
        fs.glob("**/anat/*.json")

    Descend into a Zarr asset (the tail points *inside* the Zarr store)::

        fs = DandiFileSystem("000108")
        with fs.open("path/to/image.zarr/0/0/0", "rb") as f:
            chunk = f.read()
    """

    protocol = "dandi"

    def __init__(
        self,
        dandiset: Optional[str] = None,
        version: Optional[str] = None,
        instance: Optional[Union[str, DandiInstance]] = None,
        token: Optional[str] = None,
        use_keyring: bool = True,
        asynchronous: bool = False,
        loop: Any = None,
        client_kwargs: Optional[dict] = None,
        **http_kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        dandiset:
            Identifier of a dandiset (e.g. ``"000026"``) to bind to. If given,
            relative paths are resolved against it.
        version:
            Version to bind to (e.g. ``"draft"``). Defaults to the most recent
            published version, or the draft version.
        instance:
            Name (e.g. ``"dandi"``), URL, or :class:`DandiInstance` of the DANDI
            instance. Defaults to ``"dandi"``.
        token:
            Explicit API token for private/embargoed resources. Optional; auth
            is otherwise resolved lazily (only after a 401).
        use_keyring:
            Whether to consult the system keyring during lazy auth.
        client_kwargs:
            Passed to :class:`aiohttp.ClientSession`.
        http_kwargs:
            Passed to the internal :class:`HTTPFileSystem`.
        """
        super().__init__(asynchronous=asynchronous, loop=loop)
        self._instance = (
            get_instance(instance) if instance is not None
            else get_instance("dandi")
        )
        self._dandiset_id = dandiset
        self._version_id = version
        self._token = token
        self._use_keyring = use_keyring
        self._client_kwargs = client_kwargs or {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._clients: Dict[str, DandiClient] = {}
        self._resolved: Dict[str, DandiInstance] = {}
        http_kwargs.setdefault("skip_instance_cache", True)
        self._http = HTTPFileSystem(
            asynchronous=asynchronous,
            loop=self._loop,
            client_kwargs=client_kwargs,
            **http_kwargs,
        )

    # ------------------------------------------------------------------
    #   Session lifecycle
    # ------------------------------------------------------------------

    async def set_session(self) -> aiohttp.ClientSession:
        """Create (once) and return the shared aiohttp session for REST calls."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(**self._client_kwargs)
            if not self.asynchronous:
                weakref.finalize(
                    self, self._close_session, self._loop, self._session
                )
        return self._session

    @staticmethod
    def _close_session(loop: Any, session: aiohttp.ClientSession) -> None:
        if loop is not None and loop.is_running():
            try:
                sync(loop, session.close, timeout=0.1)
                return
            except (TimeoutError, FSTimeoutError, NotImplementedError):
                pass
        connector = getattr(session, "_connector", None)
        if connector is not None:
            connector._close()

    # ------------------------------------------------------------------
    #   Public helpers
    # ------------------------------------------------------------------

    @property
    def dandiset(self) -> Optional[str]:
        """The bound dandiset identifier, if any."""
        return self._dandiset_id

    @property
    def version(self) -> Optional[str]:
        """The bound version identifier, if any."""
        return self._version_id

    @classmethod
    def for_url(cls, url: str, **kwargs: Any) -> Self:
        """Build a filesystem bound to the dandiset/instance in ``url``."""
        parsed = parse_dandi_url(url)
        return cls(
            dandiset=parsed.dandiset_id,
            version=parsed.version_id,
            instance=parsed.instance,
            **kwargs,
        )

    def s3_url(self, path: str) -> str:
        """Return the direct (S3/HTTPS) byte URL backing ``path``."""
        return sync(self.loop, self._resolve_url, path)

    # ------------------------------------------------------------------
    #   Path handling
    # ------------------------------------------------------------------

    @classmethod
    def _strip_protocol(cls, path: Any) -> str:
        path = stringify_path(path)
        if _SCHEME_RE.match(path):
            stripped = path.rstrip("/")
            return stripped or path
        return path.strip("/")

    @classmethod
    def unstrip_protocol(cls, name: str) -> str:
        """Add the ``dandi://`` scheme unless ``name`` already carries one."""
        if _SCHEME_RE.match(name):
            return name
        return "dandi://" + name

    def _parse_path(
        self, path: Any, glob: bool = False
    ) -> Tuple[DandiInstance, Optional[str], Optional[str], str]:
        """
        Split a path into (instance, dandiset_id, version_id, location).

        Absolute ``dandi://`` / ``DANDI:`` / API URLs are parsed; other paths
        are treated as relative to the bound dandiset.
        """
        path = self._strip_protocol(path)
        if _SCHEME_RE.match(path):
            parsed: ParsedDandiURL = parse_dandi_url(path, glob=glob)
            location = (parsed.glob if glob else parsed.path) or ""
            return (
                parsed.instance,
                parsed.dandiset_id,
                parsed.version_id,
                location,
            )
        if self._dandiset_id is None:
            raise ValueError(
                "This filesystem is not bound to a dandiset; "
                "pass a full dandi://<instance>/<dandiset>/... URL"
            )
        return self._instance, self._dandiset_id, self._version_id, path

    def _name(
        self,
        instance: DandiInstance,
        dandiset_id: str,
        version_id: str,
        path: str,
    ) -> str:
        """Canonical ``dandi://`` name for a root-relative asset path."""
        name = "dandi://{}/{}@{}".format(instance.name, dandiset_id, version_id)
        path = path.strip("/")
        return name + "/" + path if path else name

    async def _client_for(self, instance: DandiInstance) -> DandiClient:
        """Return (and cache) a client for ``instance``, discovering if needed."""
        if needs_discovery(instance):
            resolved = self._resolved.get(instance.api)
            if resolved is None:
                session = await self.set_session()
                resolved = await discover_instance(session, instance.api)
                self._resolved[instance.api] = resolved
            instance = resolved
        client = self._clients.get(instance.api)
        if client is None:
            token = self._token if instance.api == self._instance.api else None
            client = DandiClient(
                instance.api,
                instance.name,
                token=token,
                use_keyring=self._use_keyring,
            )
            self._clients[instance.api] = client
        return client

    # ------------------------------------------------------------------
    #   Resolution
    # ------------------------------------------------------------------

    async def _prepare(
        self, path: Any, glob: bool = False
    ) -> Tuple[DandiInstance, str, str, str, DandiClient, Any]:
        instance, dandiset_id, version_id, location = self._parse_path(
            path, glob=glob
        )
        if dandiset_id is None:
            raise FileNotFoundError(
                "No dandiset in {!r}; instance-level listing is not supported"
                .format(path)
            )
        session = await self.set_session()
        client = await self._client_for(instance)
        version_id = await client.resolve_version(
            session, dandiset_id, version_id
        )
        return instance, dandiset_id, version_id, location, client, session

    @staticmethod
    def _zarr_url(zarr_asset: dict, remainder: str) -> Optional[str]:
        base = _s3_url(zarr_asset)
        if base is None:
            return None
        base = base.rstrip("/")
        return base if not remainder else base + "/" + remainder

    def _file_url(self, client: DandiClient, asset: dict) -> str:
        url = _s3_url(asset) or _download_url(asset)
        if url:
            return url
        return "{}/assets/{}/download/".format(
            client.api_url, asset.get("asset_id")
        )

    async def _find(
        self,
        client: DandiClient,
        session: Any,
        dandiset_id: str,
        version_id: str,
        location: str,
    ) -> _Node:
        """Classify ``location`` within a dandiset version."""
        loc = location.strip("/")
        if loc == "":
            return _Node("directory", path="")

        exact = None
        async for asset in client.assets(
            session, dandiset_id, version_id, path=loc, metadata=True
        ):
            asset_path = asset.get("path", "")
            if asset_path == loc:
                exact = asset
                break
            if asset_path.startswith(loc + "/"):
                return _Node("directory", path=loc)

        if exact is not None:
            if _is_zarr(exact):
                return _Node(
                    "zarr",
                    path=loc,
                    url=self._zarr_url(exact, ""),
                    asset_id=exact.get("asset_id"),
                    zarr_id=exact.get("zarr"),
                    zarr_root=loc,
                    zarr_key="",
                    created=exact.get("created"),
                    modified=exact.get("modified"),
                    size=exact.get("size"),
                )
            return _Node(
                "file",
                path=loc,
                url=self._file_url(client, exact),
                size=exact.get("size"),
                created=exact.get("created"),
                modified=exact.get("modified"),
                asset_id=exact.get("asset_id"),
            )

        # Not an asset and not a directory prefix: maybe inside a Zarr.
        return await self._find_in_zarr(
            client, session, dandiset_id, version_id, loc
        )

    async def _find_in_zarr(
        self,
        client: DandiClient,
        session: Any,
        dandiset_id: str,
        version_id: str,
        loc: str,
    ) -> _Node:
        parts = loc.split("/")
        # Candidate prefixes of loc (excluding loc itself), longest first,
        # trying Zarr-extension prefixes first as they are the likely root.
        prefixes = ["/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]
        zarr_first = [p for p in prefixes if p.endswith(ZARR_EXTENSIONS)]
        ordered = zarr_first + [p for p in prefixes if p not in zarr_first]
        for prefix in ordered:
            asset = await client.asset_with_path(
                session, dandiset_id, version_id, prefix, metadata=True
            )
            if asset is None:
                continue
            if _is_zarr(asset):
                remainder = loc[len(prefix):].lstrip("/")
                return _Node(
                    "zarr",
                    path=loc,
                    url=self._zarr_url(asset, remainder),
                    asset_id=asset.get("asset_id"),
                    zarr_id=asset.get("zarr"),
                    zarr_root=prefix,
                    zarr_key=remainder,
                )
            # loc descends into a plain (non-Zarr) file: it cannot exist.
            return _Node("missing", path=loc)
        return _Node("missing", path=loc)

    async def _resolve_url(self, path: Any) -> str:
        instance, dandiset_id, version_id, location, client, session = (
            await self._prepare(path)
        )
        node = await self._find(
            client, session, dandiset_id, version_id, location
        )
        return self._require_file_url(node, path)

    def _require_file_url(self, node: _Node, path: Any) -> str:
        if node.kind == "file":
            return node.url
        if node.kind == "zarr" and node.zarr_key:
            if node.url is None:
                raise FileNotFoundError(str(path))
            return node.url
        if node.kind == "missing":
            raise FileNotFoundError(str(path))
        raise IsADirectoryError(str(path))

    async def _zarr_stat(
        self, client: DandiClient, session: Any, node: _Node
    ) -> Tuple[Optional[str], Optional[int]]:
        """Return ('file'|'directory'|None, size) for a location inside a Zarr."""
        if not node.zarr_id:
            # Cannot list; fall back to an HTTP HEAD on the byte URL.
            try:
                info = await self._http._info(node.url)
                return "file", info.get("size")
            except FileNotFoundError:
                return None, None
        key = node.zarr_key
        async for entry in client.zarr_files(session, node.zarr_id, prefix=key):
            entry_key = entry.get("Key", "")
            if entry_key == key:
                return "file", entry.get("Size")
            if entry_key.startswith(key + "/"):
                return "directory", None
        return None, None

    # ------------------------------------------------------------------
    #   Async filesystem API
    # ------------------------------------------------------------------

    async def _info(self, path: Any, **kwargs: Any) -> dict:  # noqa: D102
        instance, dandiset_id, version_id, location, client, session = (
            await self._prepare(path)
        )
        node = await self._find(
            client, session, dandiset_id, version_id, location
        )
        name = self._name(instance, dandiset_id, version_id, node.path)
        if node.kind == "missing":
            raise FileNotFoundError(str(path))
        if node.kind == "directory" or (
            node.kind == "zarr" and node.zarr_key == ""
        ):
            return {"name": name, "size": 0, "type": "directory"}
        if node.kind == "file":
            return {
                "name": name,
                "size": node.size,
                "type": "file",
                "created": node.created,
                "modified": node.modified,
                "asset_id": node.asset_id,
            }
        # zarr key: determine file vs directory
        ftype, size = await self._zarr_stat(client, session, node)
        if ftype is None:
            raise FileNotFoundError(str(path))
        if ftype == "directory":
            return {"name": name, "size": 0, "type": "directory"}
        return {"name": name, "size": size, "type": "file", "url": node.url}

    async def _exists(self, path: Any, **kwargs: Any) -> bool:  # noqa: D102
        try:
            instance, dandiset_id, version_id, location, client, session = (
                await self._prepare(path)
            )
        except (FileNotFoundError, ValueError):
            return False
        try:
            node = await self._find(
                client, session, dandiset_id, version_id, location
            )
        except HTTP404Error:
            return False
        if node.kind == "missing":
            return False
        if node.kind == "zarr" and node.zarr_key:
            ftype, _ = await self._zarr_stat(client, session, node)
            return ftype is not None
        return True

    async def _ls(  # noqa: D102
        self, path: Any, detail: bool = True, **kwargs: Any
    ) -> Union[List[dict], List[str]]:
        instance, dandiset_id, version_id, location, client, session = (
            await self._prepare(path)
        )
        node = await self._find(
            client, session, dandiset_id, version_id, location
        )
        if node.kind == "missing":
            raise FileNotFoundError(str(path))
        if node.kind == "file":
            info = {
                "name": self._name(instance, dandiset_id, version_id, node.path),
                "size": node.size,
                "type": "file",
                "created": node.created,
                "modified": node.modified,
                "asset_id": node.asset_id,
            }
            return [info] if detail else [info["name"]]
        if node.kind == "zarr":
            entries = await self._ls_zarr(
                client, session, instance, dandiset_id, version_id, node
            )
        else:
            entries = await self._ls_assets(
                client, session, instance, dandiset_id, version_id, location
            )
        return entries if detail else [entry["name"] for entry in entries]

    async def _ls_assets(
        self,
        client: DandiClient,
        session: Any,
        instance: DandiInstance,
        dandiset_id: str,
        version_id: str,
        location: str,
    ) -> List[dict]:
        prefix = location.strip("/")
        entries: List[dict] = []
        async for entry in client.asset_paths(
            session, dandiset_id, version_id, path_prefix=prefix or None
        ):
            entry_path = entry.get("path", "")
            name = self._name(instance, dandiset_id, version_id, entry_path)
            asset = entry.get("asset")
            if asset:
                entries.append({
                    "name": name,
                    "size": asset.get("size", entry.get("total_size")),
                    "type": "file",
                    "created": asset.get("created"),
                    "modified": asset.get("modified"),
                    "asset_id": asset.get("asset_id"),
                })
            else:
                entries.append({
                    "name": name,
                    "size": entry.get("total_size"),
                    "type": "directory",
                })
        return entries

    async def _ls_zarr(
        self,
        client: DandiClient,
        session: Any,
        instance: DandiInstance,
        dandiset_id: str,
        version_id: str,
        node: _Node,
    ) -> List[dict]:
        base = node.zarr_key.strip("/")
        base_slash = (base + "/") if base else ""
        seen = set()
        entries: List[dict] = []
        async for entry in client.zarr_files(
            session, node.zarr_id, prefix=base_slash or None
        ):
            key = entry.get("Key", "")
            if not key.startswith(base_slash):
                continue
            rest = key[len(base_slash):]
            if not rest:
                continue
            segment = rest.split("/", 1)[0]
            is_file = rest == segment
            child_key = base_slash + segment
            if child_key in seen:
                continue
            seen.add(child_key)
            full = node.zarr_root + "/" + child_key
            name = self._name(instance, dandiset_id, version_id, full)
            if is_file:
                entries.append({
                    "name": name,
                    "size": entry.get("Size"),
                    "type": "file",
                })
            else:
                entries.append({
                    "name": name, "size": None, "type": "directory",
                })
        return entries

    async def _glob(  # noqa: D102
        self, path: Any, maxdepth: Optional[int] = None, **kwargs: Any
    ) -> List[str]:
        order = kwargs.pop("order", None)
        instance, dandiset_id, version_id, pattern, client, session = (
            await self._prepare(path, glob=True)
        )
        names: List[str] = []
        async for asset in client.assets(
            session, dandiset_id, version_id, glob=pattern, order=order
        ):
            names.append(
                self._name(
                    instance, dandiset_id, version_id, asset.get("path", "")
                )
            )
        return names

    async def _cat_file(  # noqa: D102
        self,
        path: Any,
        start: Optional[int] = None,
        end: Optional[int] = None,
        **kwargs: Any,
    ) -> bytes:
        url = await self._resolve_url(path)
        return await self._http._cat_file(url, start=start, end=end, **kwargs)

    def _open(  # noqa: D102
        self,
        path: Any,
        mode: str = "rb",
        block_size: Any = None,
        autocommit: bool = True,
        cache_options: Any = None,
        **kwargs: Any,
    ) -> Any:
        if mode != "rb":
            raise NotImplementedError("DandiFileSystem is read-only")
        url = sync(self.loop, self._resolve_url, path)
        return self._http._open(
            url,
            mode=mode,
            block_size=block_size,
            cache_options=cache_options,
            **kwargs,
        )


if "dandi" not in known_implementations:
    register_implementation("dandi", DandiFileSystem)

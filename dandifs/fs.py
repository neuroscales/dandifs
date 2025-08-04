"""A `fsspec` File System for (remote) DANDI."""
# stdlib
import json
import logging
import re
from os import PathLike
from typing import Iterator, Tuple
from urllib.parse import unquote as url_unquote

# externals
import requests
from fsspec import register_implementation
from fsspec.implementations.http import HTTPFileSystem
from fsspec.registry import known_implementations
from fsspec.spec import AbstractBufferedFile, AbstractFileSystem
from fsspec.utils import stringify_path, tokenize

# internals
from .api import Asset, DandiAPIClient, Dandiset, DandisetVersion, ZarrAsset
from .exceptions import NotFoundError
from .instance import DandiInstance, get_instance
from .parser import ParsedDandiURL, parse_dandi_url
from .utils import ensure_datetime

LOG = logging.getLogger(__name__)


class DandiFileSystem(AbstractFileSystem):
    """
    A file system that browses through a remote dandiset.

    Examples
    --------
    Load and parse a remote file
    ```python
    from dandifs import RemoteDandiFileSystem
    import json
    fs = RemoteDandiFileSystem()
    with fs.open('dandi://dandi/000026/rawdata/sub-I38/ses-MRI/anat/'
                 'sub-I38_ses-MRI-echo-4_flip-4_VFA.json') as f:
        info = json.load(f)
    ```

    The 'dandi://' protocol is registered with fsspec, so the same
    result can be achived by
    ```python
    import fsspec
    import json
    with fsspec.open('dandi://dandi/000026/rawdata/sub-I38/ses-MRI/anat/'
                     'sub-I38_ses-MRI-echo-4_flip-4_VFA.json') as f:
        info = json.load(f)
    ```

    Browse a dataset
    ```python
    from dandifs import RemoteDandiFileSystem
    fs = RemoteDandiFileSystem('000026')
    fs.glob('**/anat/*.json')
    ```
    or
    ```python
    from dandifs import RemoteDandiFileSystem
    fs = RemoteDandiFileSystem()
    fs.glob('dandi://dandi/000026/**/anat/*.json')
    ```

    """

    def __init__(
        self,
        dandiset: Dandiset | str | None = None,
        version: DandisetVersion | str | None = None,
        instance: DandiInstance | str | None = None,
        client: DandiAPIClient | str | None = None,
        **http_kwargs
    ) -> None:
        """
        Initialise a remote DANDI file system.

        The root of a DANDI file system is a dandiset at a given version.

        Parameters
        ----------
        dandiset : Dandiset | str, optional
            The identifier of a dandiset (e.g., `'000026'`).
        version : DandisetVersion | str, optional
            The version of the dandiset to query (e.g., `'draft'`)
        instance : DandiInstance | str, optional
            The identifier of a DANDI instance (e.g., `'DANDI'`) or
            its url.
        client : DandiAPIClient | str
            A client to a DANDI instance, or api URL.

        Other Parameters
        ----------------
        http_kwargs: key-value
            Any other parameters passed on to the HTTP file system
        """
        self._httpfs = HTTPFileSystem(**http_kwargs)
        super().__init__()
        self._client: DandiAPIClient | None = None
        self._dandiset: DandisetVersion | None = None
        if isinstance(version, DandisetVersion):
            self._dandiset = version
        elif isinstance(dandiset, Dandiset):
            self._dandiset = dandiset.version(version)
        elif not isinstance(client, DandiAPIClient):
            self._client = DandiAPIClient(client or instance)
            if dandiset:
                self._dandiset = Dandiset(dandiset, client).version(version)
                self._client = None
            else:
                self._dandiset = None

    # ------------------------------------------------------------------
    #   DANDI-specific helpers
    # ------------------------------------------------------------------

    @property
    def dandiset(self) -> DandisetVersion | None:
        """Access dandiset."""
        return self._dandiset

    @dandiset.setter
    def dandiset(self, x: Dandiset | DandisetVersion | None) -> None:
        """Assign dandiset."""
        if x:
            self._client = None
        elif self._dandiset:
            self._client = self._dandiset._client
        if isinstance(x, Dandiset):
            x = x.version()
        self._dandiset = x

    @property
    def client(self) -> DandiAPIClient:
        """Access dandi client."""
        return self.dandiset.client if self.dandiset else self._client

    @client.setter
    def client(self, x: DandiAPIClient) -> None:
        """Assign dandi client."""
        if self.dandiset:
            raise ValueError('Cannot assign a DANDI client to a FileSystem '
                             'that is already linked to a dandiset. '
                             'Unassign the dandiset first.')
        self._client = x

    @classmethod
    def for_url(cls, url: str) -> "DandiFileSystem":
        """
        Instantiate a FileSystem that interacts with the correct
        DANDI instance for a given url.
        """
        parsed = parse_dandi_url(url)
        return cls(
            dandiset=parsed.dandiset_id,
            version=parsed.version_id,
            instance=parsed.instance,
        )

    def _get_json(self, url: str) -> dict:
        with self._httpfs.open(url, "rt") as f:
            info = json.load(f)
        return info

    def s3_url(self, path: str, **kwargs) -> str:
        """Get the the asset url on AWS S3."""
        dandiset, asset = self.get_dandiset(path, **kwargs)
        if not isinstance(path, Asset):
            try:
                asset = self._auth_apply(
                    lambda: dandiset.get_asset_by_path(asset),
                    dandiset.client, kwargs.get("auth", None)
                )

            except NotFoundError:
                path = asset.rstrip("/")
                path_prefix, path_suffix = path, ''
                while path_prefix:

                    *path_prefix, new_suffix = path_prefix.split('/')
                    if not path_suffix:
                        path_suffix = new_suffix
                    else:
                        path_suffix = path_suffix.split('/')
                        path_suffix = '/'.join([new_suffix, *path_suffix])
                    path_prefix = '/'.join(path_prefix)

                    try:
                        asset = self._auth_apply(
                            lambda: dandiset.get_asset_by_path(path_prefix),
                            dandiset.client, kwargs.get("auth", None)
                        )
                        url = self._s3_url_from_asset(asset).rstrip("/")
                        url += "/" + path_suffix
                        return url
                    except NotFoundError:
                        continue
                raise NotFoundError(path)

        return self._s3_url_from_asset(asset)

    def _parse(
        self, url: str, glob: bool = False
    ) -> Tuple[DandiAPIClient, ParsedDandiURL]:
        protocols = ("http://", "https://", "dandi://", "DANDI:")
        url = stringify_path(url).strip('/')

        if not url.startswith(protocols):
            if self.dandiset:
                instance = self.dandiset.instance.name
                dandiset = self.dandiset.dandiset_id
                dandiset += "@" + self.dandiset.version_id
                url = f"dandi://{instance}/{dandiset}/{url}"
            else:
                if not self.client:
                    self.client = DandiAPIClient()
                instance = self.client.dandi_instance.name
                url = f"dandi://{instance}/{url}"

        parsed = parse_dandi_url(url, glob=glob)
        client = self.client
        if client.api_url != parsed.instance.api:
            client = DandiAPIClient.for_dandi_instance(parsed.instance)

        return client, parsed

    # ------------------------------------------------------------------
    #   FileSystem API
    # ------------------------------------------------------------------

    def ls(  # noqa: D102
        self,
        path: str | PathLike,
        detail: bool = True,
        **kwargs
    ) -> list[str] | list[dict]:
        client, parsed = self._parse(path)

        assets = kwargs.pop('assets', None)
        if assets is None:
            dandiset = kwargs.pop('dandiset', None)
            if not dandiset:
                dandiset = Dandiset(parsed.dandiset_id, client)
                dandiset = dandiset.version(parsed.version_id)
            assets = dandiset.assets(path)

        entries = []
        full_dirs = set()

        def getdate(asset: Asset, field: str) -> str:
            if field not in asset.content:
                return None
            return ensure_datetime(asset.content[field]).isoformat

        assets, assets_in = [], assets
        for asset in assets_in:
            size = getattr(asset, 'size', None)
            created = getdate(asset, 'created')
            modified = getdate(asset, 'modified')
            identifier = getattr(asset, 'identifer', None)
            asset = getattr(asset, 'path', asset)
            # 1) is the input path exactly this asset?
            asset = asset[len(path):].strip('/')
            if not asset:
                entries.append({
                    'name': path,
                    'size': size,
                    'created': created,
                    'modified': modified,
                    'identifier': identifier,
                    'type': 'file',
                })
                continue
            # 2) look at the first level under `path`
            name = asset.split('/')[0]
            fullpath = path + '/' + name
            if '/' not in asset:
                # 3) this asset is a file directly under `path`
                entries.append({
                    'name': fullpath,
                    'size': size,
                    'created': created,
                    'modified': modified,
                    'identifier': identifier,
                    'type': 'file',
                })
                continue
            else:
                # 4) this asset is a file a few levels under `path`
                # -> we do not list the path but list the directory
                if fullpath not in full_dirs:
                    entries.append({
                        'name': fullpath,
                        'size': None,
                        'type': 'directory',
                    })
                    full_dirs.add(fullpath)
            assets.append(path + '/' + asset)

        if detail:
            return entries
        else:
            return [entry['name'] for entry in entries]

    def checksum(self, path: str, **kwargs) -> str:  # noqa: D102
        # we override fsspec's default implementation when path is a
        # directory (since in this case there is no created/modified date)
        client, parsed = self._parse(path, glob=True)
        dandiset = kwargs.pop('dandiset', None)
        if not dandiset:
            dandiset, path = self.get_dandiset(path)
        assets = dandiset.get_assets_with_path_prefix(path)
        return tokenize(assets)

    def glob(  # noqa: D102
        self,
        path: str,
        order: str | None = None,
        **kwargs
    ) -> Iterator[str]:
        # we override fsspec's default implementation (which uses find)
        # to leverage the more efficient `get_assets_by_glob` from dandi
        #
        # order : [-]{created, modified, path}
        #
        # TODO: implement fsspec `maxdepth` keyword
        dandiset = kwargs.pop('dandiset', None)
        if not dandiset:
            dandiset, path = self.get_dandiset(path)
        assets = dandiset.get_assets_by_glob(path, order)
        for asset in assets:
            yield asset.path

    def exists(self, path: str, **kwargs) -> bool:  # noqa: D102
        # we override fsspec's default implementation (which uses info)
        # to avoid calls to ls (which calls get_assets_by_path on the
        # *parent* and is therefore slower)
        dandiset = kwargs.pop('dandiset', None)
        if not dandiset:
            dandiset, path = self.get_dandiset(path)
        if isinstance(path, Asset):
            return True
        # check if it is a file
        try:
            dandiset.get_asset_by_path(path)
            return True
        except NotFoundError:
            pass
        # check if it is a directory
        path = path.rstrip('/') + '/'
        assets = dandiset.get_assets_with_path_prefix(path)
        try:
            next(assets)
            return True
        except StopIteration:
            pass
        # it might be a path to something inside a zarr -- let's try to find it
        try:
            return self._httpfs.exists(self.s3_url(path, dandiset=dandiset))
        except NotFoundError:
            pass
        return False

    def open(  # noqa: D102
        self,
        path: str,
        *args,
        **kwargs
    ) -> AbstractBufferedFile:
        path = self._maybe_to_s3(path)
        return self._httpfs.open(path, *args, **kwargs)


if "dandi" not in known_implementations:
    register_implementation("dandi", DandiFileSystem)

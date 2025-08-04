# NOTICE
#   This file was copied and modified from [dandi-cli] dandi/dandiapi.py,
#   which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""DANDI REST API."""
# std
import os
import posixpath
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import sleep
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Iterator, Sequence

# external
import requests
import tenacity

# internal
from . import get_logger
from .confirm import confirm
from .consts import REQUEST_RETRIES, RETRY_STATUSES
from .exceptions import HTTP404Error, NotFoundError
from .instance import DandiInstance, get_instance
from .keyring import keyring_lookup, keyring_save
from .utils import (
    USER_AGENT,
    get_retry_after,
    is_interactive,
    is_page2_url,
    joinurl,
)

if TYPE_CHECKING:
    from typing_extensions import Self

LOG = get_logger()


def _normalize_path(path: str) -> str:
    """
    Helper to normalize path before passing it to the server.

    We and API call it "path" but it is really a "prefix" with inherent
    semantics of containing directory divider '/' and referring to a
    directory when terminated with '/'.
    """
    # Server (now) expects path to be a proper prefix, so to account for user
    # possibly specifying ./ or some other relative paths etc, let's normalize
    # the path.
    # Ref: https://github.com/dandi/dandi-cli/issues/1452
    path_normed = posixpath.normpath(path)
    if path_normed == ".":
        path_normed = ""
    elif path.endswith("/"):
        # we need to make sure that we have a trailing slash if we had
        # it before
        path_normed += "/"
    if path_normed != path:
        LOG.debug("Normalized path %r to %r", path, path_normed)
    return path_normed


# Following class is loosely based on GirderClient, with authentication etc
# being stripped.
# TODO: add copyright/license info
class RESTFullAPIClient:
    """
    Base class for a JSON-based HTTP(S) client for interacting with a given
    base API URL.

    All request methods can take either an absolute URL or a slash-separated
    path; in the latter case, the path is appended to the base API URL
    (separated by a slash) in order to determine the actual URL to make the
    request of.

    `RESTFullAPIClient` instances are usable as context managers, in which case
    they will close their associated session on exit.
    """

    def __init__(
        self,
        api_url: str,
        session: requests.Session | None = None,
        headers: dict | None = None,
    ) -> None:
        """
        :param str api_url: The base HTTP(S) URL to prepend to request paths
        :param session: an optional `requests.Session` instance to use; if not
            specified, a new session is created
        :param headers: an optional `dict` of headers to send in every request
        """
        self.api_url = api_url
        if session is None:
            session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        if headers is not None:
            session.headers.update(headers)
        self.session = session
        #: Default number of items to request per page when paginating (`None`
        #: means to use the server's default)
        self.page_size: int | None = None
        #: How many pages to fetch at once when parallelizing pagination
        self.page_workers: int = 5

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.session.close()

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        data: Any = None,
        files: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
        json_resp: bool = True,
        retry_statuses: Sequence[int] = (),
        retry_if: Callable[[requests.Response], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        This method looks up the appropriate method, constructs a request URL
        from the base URL, path, and parameters, and then sends the request. If
        the method is unknown or if the path is not found, an exception is
        raised; otherwise, a JSON object is returned with the response.

        This is a convenience method to use when making basic requests that do
        not involve multipart file data that might need to be specially encoded
        or handled differently.

        :param method: The HTTP method to use in the request (GET, POST, etc.)
        :type method: str
        :param path: A string containing the path elements for this request
        :type path: str
        :param params: A dictionary mapping strings to strings, to be used
            as the key/value pairs in the request parameters.
        :type params: dict
        :param data: A dictionary, bytes or file-like object to send in the
            body.
        :param files: A dictionary of 'name' => file-like-objects for multipart
            encoding upload.
        :type files: dict
        :param json: A JSON object to send in the request body.
        :type json: dict
        :param headers: If present, a dictionary of headers to encode in the
            request.
        :type headers: dict
        :param json_resp: Whether the response should be parsed as JSON. If
            False, the raw response object is returned. To get the raw binary
            content of the response, use the ``content`` attribute of the
            return value, e.g.

            .. code-block:: python

                resp = client.get('my/endpoint', json_resp=False)
                print(resp.content)  # Raw binary content
                print(resp.headers)  # Dict of headers

        :type json_resp: bool
        :param retry_statuses: a sequence of HTTP response status codes to
            retry in addition to `dandi.consts.RETRY_STATUSES`
        :param retry_if: an optional predicate applied to a failed HTTP
            response to test whether to retry
        """
        retry_statuses = [*RETRY_STATUSES, *retry_statuses]

        url = self.get_url(path)

        if headers is None:
            headers = {}
        if json_resp and "accept" not in headers:
            headers["accept"] = "application/json"

        LOG.debug("%s %s", method.upper(), url)

        try:
            for i, attempt in enumerate(tenacity.Retrying(
                wait=tenacity.wait_exponential(exp_base=1.25, multiplier=1.25),
                # urllib3's ConnectionPool isn't thread-safe, so we
                # sometimes hit ConnectionErrors on the start of an upload.
                # Retry when this happens.
                # Cf. <https://github.com/urllib3/urllib3/issues/951>.
                retry=tenacity.retry_if_exception_type(
                    (requests.ConnectionError, requests.HTTPError)
                ),
                stop=tenacity.stop_after_attempt(REQUEST_RETRIES),
                reraise=True,
            )):
                with attempt:
                    result = self.session.request(
                        method,
                        url,
                        params=params,
                        data=data,
                        files=files,
                        json=json,
                        headers=headers,
                        **kwargs,
                    )
                    if result.status_code in retry_statuses or (
                        retry_if is not None and retry_if(result)
                    ):
                        attempt_number = attempt.retry_state.attempt_number
                        if attempt_number < REQUEST_RETRIES:
                            LOG.warning(
                                "Will retry: "
                                "Error %d while sending %s request to %s: %s",
                                result.status_code,
                                method,
                                url,
                                result.text,
                            )
                            if data is not None and hasattr(data, "seek"):
                                data.seek(0)
                        if retry_after := get_retry_after(result):
                            LOG.debug(
                                "Sleeping for %d seconds as instructed in "
                                "response (in addition to tenacity imposed)",
                                retry_after,
                            )
                            sleep(retry_after)
                        result.raise_for_status()
        except Exception as e:
            if isinstance(e, requests.HTTPError):
                LOG.error(
                    "HTTP request failed repeatedly: "
                    "Error %d while sending %s request to %s: %s",
                    e.response.status_code if e.response is not None else "?",
                    method,
                    url,
                    e.response.text if e.response is not None else "?",
                )
            else:
                LOG.exception("HTTP connection failed")
            raise

        if i > 0:
            LOG.info(
                "%s %s succeeded after %d retr%s",
                method.upper(),
                url,
                i,
                "y" if i == 1 else "ies",
            )

        LOG.debug("Response: %d", result.status_code)

        # If success, return the json object. Otherwise throw an exception.
        if not result.ok:
            msg = (
                f"Error {result.status_code} while sending {method} "
                "request to {url}"
            )
            if result.status_code == 409:
                # Blob exists on server; log at DEBUG level
                LOG.debug("%s: %s", msg, result.text)
            else:
                LOG.error("%s: %s", msg, result.text)
            if len(result.text) <= 1024:
                msg += f": {result.text}"
            else:
                msg += (
                    f": {result.text[:1024]}... [{len(result.text)}-char "
                    "response truncated]"
                )
            if result.status_code == 404:
                raise HTTP404Error(msg, response=result)
            else:
                raise requests.HTTPError(msg, response=result)

        if json_resp:
            if result.text.strip():
                return result.json()
            else:
                return None
        else:
            return result

    def get_url(self, path: str) -> str:
        """
        Append a slash-separated ``path`` to the instance's base URL.  The two
        components are separated by a single slash, removing any excess slashes
        that would be present after naïve concatenation.

        If ``path`` is already an absolute URL, it is returned unchanged.
        """
        return joinurl(self.api_url, path)

    def get(self, path: str, **kwargs: Any) -> Any:
        """
        Convenience method to call `request()` with the 'GET' HTTP method.
        """
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        """
        Convenience method to call `request()` with the 'POST' HTTP method.
        """
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        """
        Convenience method to call `request()` with the 'PUT' HTTP method.
        """
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        """
        Convenience method to call `request()` with the 'DELETE' HTTP method.
        """
        return self.request("DELETE", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        """
        Convenience method to call `request()` with the 'PATCH' HTTP method.
        """
        return self.request("PATCH", path, **kwargs)

    def paginate(
        self,
        path: str,
        page_size: int | None = None,
        params: dict | None = None,
    ) -> Iterator:
        """
        Paginate through the resources at the given path: GET the path, yield
        the values in the ``"results"`` key, and repeat with the URL in the
        ``"next"`` key until it is ``null``.

        If the first ``"next"`` key is the same as the initially-requested URL
        but with the ``page`` query parameter set to ``2``, then the remaining
        pages are fetched concurrently in separate threads, `page_workers`
        (default 5) at a time.  This behavior requires the initial response to
        contain a ``"count"`` key giving the number of items across all pages.

        :param page_size:
            If non-`None`, overrides the client's `page_size` attribute for
            this sequence of pages
        """
        if page_size is None:
            page_size = self.page_size
        if page_size is not None:
            if params is None:
                params = {}
            params["page_size"] = page_size

        resp = self.get(path, params=params, json_resp=False)
        r = resp.json()
        if r["next"] is not None:
            page1 = resp.history[0].url if resp.history else resp.url
            if not is_page2_url(page1, r["next"]):
                if os.environ.get("DANDI_PAGINATION_DISABLE_FALLBACK"):
                    raise RuntimeError(
                        f"API server changed pagination strategy: {page1} URL"
                        f" is now followed by {r['next']}"
                    )
                else:
                    while True:
                        yield from r["results"]
                        if r.get("next"):
                            r = self.get(r["next"])
                        else:
                            return
        yield from r["results"]
        if r["next"] is None:
            return

        if page_size is None:
            page_size = len(r["results"])
        pages = (r["count"] + page_size - 1) // page_size

        def get_page(pageno: int) -> list:
            params2 = params.copy() if params is not None else {}
            params2["page"] = pageno
            results = self.get(path, params=params2)["results"]
            assert isinstance(results, list)
            return results

        with ThreadPoolExecutor(max_workers=self.page_workers) as pool:
            futures = [pool.submit(get_page, i) for i in range(2, pages + 1)]
            try:
                for f in futures:
                    yield from f.result()
            finally:
                for f in futures:
                    f.cancel()


class DandiAPIClient(RESTFullAPIClient):
    """A client for interacting with a DANDI API server."""

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        dandi_instance: DandiInstance | None = None,
    ) -> None:
        """
        Construct a client instance for the given API URL or DANDI instance
        (mutually exclusive options).  If no URL or instance is supplied, the
        instance specified by the :envvar:`DANDI_INSTANCE` environment variable
        (default value: ``"dandi"``) is used.

        :param str api_url: Base API URL of the server to interact with.
            - For DANDI production, use  ``"https://api.dandiarchive.org/api"``
            - For DANDI staging, use
              ``"https://api-staging.dandiarchive.org/api"``
        :param str token: User API Key. Note that different instance APIs have
            different keys.
        """
        if api_url is None:
            if dandi_instance is None:
                instance_name = os.environ.get("DANDI_INSTANCE", "dandi")
                dandi_instance = get_instance(instance_name)
            api_url = dandi_instance.api
        elif dandi_instance is not None:
            raise ValueError(
                "api_url and dandi_instance are mutually exclusive"
            )
        else:
            dandi_instance = get_instance(api_url)
        super().__init__(api_url)
        self.dandi_instance: DandiInstance = dandi_instance
        if token is not None:
            self.authenticate(token)

    @classmethod
    def for_dandi_instance(
        cls,
        instance: str | DandiInstance,
        token: str | None = None,
        authenticate: bool = False,
    ) -> "DandiAPIClient":
        """
        Construct a client instance for the server identified by ``instance``
        (either the name of a registered DANDI instance or a
        `DandiInstance` instance) and an optional authentication token/API key.
        If no token is supplied and ``authenticate`` is true,
        `dandi_authenticate()` is called on the instance before returning it.
        """
        client = cls(dandi_instance=get_instance(instance), token=token)
        if token is None and authenticate:
            client.dandi_authenticate()
        return client

    def authenticate(self, token: str, save_to_keyring: bool = False) -> None:
        """
        Set the authentication token/API key used by the `DandiAPIClient`.
        Before setting the token, a test request to ``/auth/token`` is made to
        check the token's validity; if it fails, a `requests.HTTPError` is
        raised.

        If ``save_to_keyring`` is true, then (after querying ``/auth/token``
        but before setting the API key used by the client), the token is saved
        in the user's keyring at the same location as used by
        `dandi_authenticate()`.

        .. versionchanged:: 0.53.0

            ``save_to_keyring`` added
        """
        # Fails if token is invalid:
        self.get("/auth/token", headers={"Authorization": f"token {token}"})
        if save_to_keyring:
            keyring_save(self._get_keyring_ids()[1], "key", token)
            LOG.debug("Stored key in keyring")
        self.session.headers["Authorization"] = f"token {token}"

    def dandi_authenticate(self) -> None:
        """
        Acquire and set the authentication token/API key used by the
        `DandiAPIClient`.  If the :envvar:`DANDI_API_KEY` environment variable
        is set, its value is used as the token.  Otherwise, the token is looked
        up in the user's keyring under the service
        ":samp:`dandi-api-{INSTANCE_NAME}`" [#auth]_ and username "``key``".
        If no token is found there, the user is prompted for the token, and, if
        it proves to be valid, it is stored in the user's keyring.

        .. [#auth] E.g., "``dandi-api-dandi``" for the production server or
                   "``dandi-api-dandi-staging``" for the staging server
        """
        # Shortcut for advanced folks
        instance = self.dandi_instance.name
        api_key = os.environ.get(f"{instance.upper()}_API_KEY", None)
        if api_key:
            LOG.debug(
                f"Authentification using api key from "
                f"{instance.upper()}_API_KEY environment variable."
            )
            try:
                self.authenticate(api_key)
            except requests.HTTPError:
                LOG.debug(
                    f"Authentification using api key from "
                    f"{instance.upper()}_API_KEY environment variable failed. "
                    f"Trying with keyring."
                )
                api_key = None
        client_name, app_id = self._get_keyring_ids()
        keyring_backend, api_key = keyring_lookup(app_id, "key")
        key_from_keyring = api_key is not None
        while True:
            if not api_key:
                api_key = input(f"Please provide API Key for {client_name}: ")
                key_from_keyring = False
            try:
                LOG.debug(
                    "Using API key from %s",
                    {True: "keyring", False: "user input"}[key_from_keyring],
                )
                self.authenticate(api_key)
            except requests.HTTPError:
                if is_interactive() and confirm(
                    "API key is invalid; enter another?"
                ):
                    api_key = None
                    continue
                else:
                    raise
            else:
                if not key_from_keyring:
                    keyring_backend.set_password(app_id, "key", api_key)
                    LOG.debug("Stored key in keyring")
                break

    def _get_keyring_ids(self) -> tuple[str, str]:
        client_name = self.dandi_instance.name
        return (client_name, f"dandi-api-{client_name}")

    @property
    def _instance_id(self) -> str:
        return self.dandi_instance.name.upper()

    def dandiset(self, dandiset_id: str) -> "Dandiset":  # noqa: D102
        return Root(self).dandiset(dandiset_id)

    def dandisets(self, *args, **kwargs) -> Iterator["Dandiset"]:  # noqa: D102
        return Root(self).dandisets(*args, **kwargs)

    def asset(self, asset_id: str) -> "Asset":  # noqa: D102
        return Root(self).asset(asset_id)


@dataclass
class APIBase:
    """Base class for API paths."""

    @property
    def apipath(self) -> str:
        """API path."""
        ...

    @property
    def content(self) -> dict:
        """JSON response to get(path)."""
        if self._content is None:
            self.populate()
        return self._content

    @property
    def info(self) -> dict:
        """JSON response to get(path/info)."""
        if self._info is None:
            self.populate_info()
        return self._info

    @property
    def client(self) -> DandiAPIClient:
        """Existing or newly instantiated client."""
        if self._client is None:
            self._client = DandiAPIClient()
        return self._client

    @client.setter
    def client(self, value: DandiAPIClient) -> None:
        """Set client."""
        self._client = value

    @property
    def api_url(self) -> DandiInstance:
        """API URL."""
        return self.client.api_url

    @property
    def instance(self) -> DandiInstance:
        """DANDI instance."""
        return get_instance(self.api_url)

    def get(self, *args, **kwargs) -> Any:  # noqa: D102
        return self.client.get(*args, **kwargs)

    def post(self, *args, **kwargs) -> Any:  # noqa: D102
        return self.client.post(*args, **kwargs)

    def put(self, *args, **kwargs) -> Any:  # noqa: D102
        return self.client.put(*args, **kwargs)

    def delete(self, *args, **kwargs) -> Any:  # noqa: D102
        return self.client.delete(*args, **kwargs)

    def patch(self, *args, **kwargs) -> Any:  # noqa: D102
        return self.client.patch(*args, **kwargs)

    def exists(self) -> bool:
        """True if get(path) returns 200."""
        try:
            self.content
        except requests.HTTPError:
            return False

    def populate(self, overwrite: bool = False) -> None:
        """Populate content with get(path)."""
        if overwrite or self._content is None:
            self._content = self.get(self.apipath)

    def populate_info(self, overwrite: bool = False) -> None:
        """Populate info with get(path/info)."""
        if overwrite or self._info is None:
            self._info = self.get(self.apipath + "/info")


class Root(APIBase):
    """Root API path."""

    _client: DandiAPIClient = DandiAPIClient()
    _content: None = None
    _info: dict | None = None

    @property
    def apipath(self) -> str:
        """API path."""
        return ""

    def dandiset(self, dandiset_id: str) -> "Dandiset":
        """Return a given dandiset."""
        return Dandiset(dandiset_id, self._client)

    def dandisets(
        self,
        search: str | None = None,
        user: str | None = None,
        starred: bool | None = None,
        draft: bool | None = None,
        empty: bool | None = None,
        embargoed: bool | None = None,
        ordering: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Iterator["Dandiset"]:
        """Iterator across dandisets in this instance."""
        apipath = "/dandisets"

        params = {}
        if search is not None:
            params['search'] = search
        if user is not None:
            params['user'] = user
        if starred is not None:
            params['starred'] = starred
        if draft is not None:
            params['draft'] = draft
        if empty is not None:
            params['empty'] = empty
        if embargoed is not None:
            params['embargoed'] = embargoed
        if ordering is not None:
            params['ordering'] = ordering
        if page is not None:
            if page is not None:
                params['page'] = page
            if page_size is not None:
                params['page_size'] = page_size
            info = self.get(apipath, params=params)
            for dandiset in info["results"]:
                yield Dandiset(
                    dandiset["identifier"], self, self.client, dandiset
                )
        else:
            for asset in self.client.paginate(apipath, page_size, params):
                yield Dandiset(
                    dandiset["identifier"], self, self.client, dandiset
                )

    def asset(self, asset_id: str) -> "Asset":
        """Return a given dandiset."""
        return Asset(asset_id, None, self._client)


class Dandiset(APIBase):
    """API path to a dandiset."""

    dandiset_id: str
    _client: DandiAPIClient = DandiAPIClient()
    _content: dict | None = None
    _info: dict | None = None

    @property
    def apipath(self) -> str:
        """API path."""
        return f"/dandisets/{self.dandiset_id}"

    def versions(self) -> Iterator["DandisetVersion"]:
        """Iterator across versions of this dandiset."""
        info = self.get(f"{self.apipath}/versions")
        for ver in info["results"]:
            yield DandisetVersion(self, ver["version"], self.client, ver)

    def version(
        self, version_id: str | None = None
    ) -> "DandisetVersion" | None:
        """Most recent version of this dandiset."""
        if version_id:
            return DandisetVersion(self, version_id, self.client)
        if "most_recent_published_version" in self.content:
            ver = self.content["most_recent_published_version"]
        elif "draft_version" in self.content:
            ver = self.content["draft_version"]
        else:
            return None
        return DandisetVersion(self, ver["version"], self.client, ver)

    def draft(self) -> "DandisetVersion" | None:
        """Draft version of this dandiset."""
        if "draft_version" in self.content:
            ver = self.content["draft_version"]
            return DandisetVersion(self, ver["version"], self.client, ver)
        else:
            return None

    def asset(self, asset_id: str) -> "Asset":  # noqa: D102
        return self.version().asset(asset_id)

    def asset_with_path(self, path: str) -> "Asset":  # noqa: D102
        return self.version().asset_with_path(path)

    def assets(self, *args, **kwargs) -> Iterator["Asset"]:  # noqa: D102
        return self.version().assets(*args, **kwargs)

    def asset_paths(  # noqa: D102
        self, *args, **kwargs
    ) -> Iterator["AssetPath"]:
        return self.version().asset_paths(*args, **kwargs)


@dataclass
class DandisetVersion(APIBase):
    """API path to a specific version of a dandiset."""

    dandiset: Dandiset
    version_id: str
    _client: DandiAPIClient = DandiAPIClient()
    _content: dict | None = None
    _info: dict | None = None

    @property
    def apipath(self) -> str:
        """API path."""
        return self.dandiset.apipath + f"/versions/{self.version_id}"

    @property
    def dandiset_id(self) -> str:
        """Dandiset ID (read from parent Dandiset)."""
        return self.dandiset.dandiset_id

    def asset(self, asset_id: str) -> "Asset":
        """Return an asset."""
        return Asset(asset_id, self, self.client)

    def asset_with_path(self, path: str) -> "Asset":
        """Return an asset."""
        path = _normalize_path(path)
        for asset in self.assets(path=path):
            if asset.content["path"] == path:
                return asset
        raise NotFoundError(f"No asset at path {path!r}")

    def assets(
        self,
        path: str | None = None,
        glob: str | None = None,
        order: str | None = None,
        zarr: bool | None = None,
        metadata: bool | None = None,
        page_size: int | None = None,
        page: int | None = None,
    ) -> Iterator["Asset"]:
        """Iterator across dandiset assets.

        :param path: Filter assets by path prefix
        :param glob: Filter assets using a glob pattern
        :param order: Order by {"created","modified","path"}.
                      Prepend "-" to reverse.
        :param zarr: Only return assets that are [not] Zarr
        :param metadata: Return the metadata of the assets in the response
        :param page_size: See `page`
        :param page: Return asset range `page_size*page:page_size*(page+1)`
        """
        apipath = f"{self.apipath}/assets"

        params = {}
        if path is not None:
            params['path'] = path
        if glob is not None:
            params['glob'] = glob
        if zarr is not None:
            params['order'] = order
        if metadata is not None:
            params['zarr'] = zarr
        if order is not None:
            params['metadata'] = metadata
        if page is not None:
            if page is not None:
                params['page'] = page
            if page_size is not None:
                params['page_size'] = page_size
            info = self.get(apipath, params=params)
            for asset in info["results"]:
                yield Asset(asset["asset_id"], self, self.client, asset)
        else:
            for asset in self.client.paginate(apipath, page_size, params):
                yield Asset(asset["asset_id"], self, self.client, asset)

    def asset_paths(
        self,
        page: int | None = None,
        page_size: int | None = None,
        path_prefix: str | None = None,
    ) -> Iterator["AssetPath"]:
        """Iterator across dandiset asset paths.

        :param page: Return asset range `page_size*page:page_size*(page+1)`
        :param page_size: See `page`
        :param path_prefix: Only return paths that start with this prefix
        """
        apipath = f"{self.apipath}/assets/paths"

        params = {}
        if page is not None:
            params['page'] = page
        if page_size is not None:
            params['page_size'] = page_size
        if path_prefix is not None:
            params['path_prefix'] = path_prefix

        if page is not None:
            if page is not None:
                params['page'] = page
            if page_size is not None:
                params['page_size'] = page_size
            info = self.get(apipath, params=params)
            for asset in info["results"]:
                yield AssetPath(asset["path"], self, self.client, asset)
        else:
            for asset in self.client.paginate(apipath, page_size, params):
                yield AssetPath(asset["path"], self, self.client, asset)


@dataclass
class Asset(APIBase):
    """API path to an asset."""

    asset_id: str
    dandiset_version: DandisetVersion
    _client: DandiAPIClient = DandiAPIClient()
    _content: dict | None = None
    _info: dict | None = None

    @property
    def apipath(self) -> str:
        """API path."""
        return self.dandiset_version.apipath + f"/assets/{self.asset_id}"

    @property
    def dandiset_id(self) -> str:
        """Dandiset ID (read from parent DandisetVersion)."""
        return self.dandiset_version.dandiset_id

    @property
    def version_id(self) -> str:
        """Dandiset version ID (read from parent DandisetVersion)."""
        return self.dandiset_version.version_id

    def is_zarr(self) -> True:
        """True if asset is a Zarr."""
        return "zarr" in self.content

    def as_zarr(self) -> "ZarrAsset":
        """Zarr asset specialization."""
        return ZarrAsset(self.content["zarr"], self, self._client)

    @property
    def download_url(self) -> str:
        """URL to download the asset (.../download/).

        This URL can be used to download the asset in the browser,
        or to read its bytes (with streaming) using requests.

        It does not exist for zarr assets, which are multi-file assets.
        """
        try:
            urls = self._content["metadata"]["contentUrl"]
            for url in urls:
                if url.endswith("/download/"):
                    return url
        except KeyError:
            ...
        return None

    @property
    def fs_url(self) -> str:
        """URL to access the (single- ot multi-file) asset.

        This URL exists even for zarr assets, in which case it preserves
        the directory structure.

        It can be opened with the fsspec HTTP file system.
        """
        try:
            urls = self._content["metadata"]["contentUrl"]
            for url in urls:
                if not url.endswith("/download/"):
                    return url
        except KeyError:
            ...
        return None


@dataclass
class AssetPath(APIBase):
    """Dandi path to an asset."""

    path: str
    dandiset_version: DandisetVersion
    client: DandiAPIClient = DandiAPIClient()
    _content: dict | None = None

    @property
    def dandiset_id(self) -> str:
        """Dandiset ID (read from parent DandisetVersion)."""
        return self.dandiset_version.dandiset_id

    @property
    def version_id(self) -> str:
        """Dandiset version ID (read from parent DandisetVersion)."""
        return self.dandiset_version.version_id


@dataclass
class ZarrAsset(APIBase):
    """API path to a zarr asset."""

    zarr_id: str | None
    asset: Asset | None = None
    _client: DandiAPIClient | None = None

    @property
    def apipath(self) -> str:
        """API path."""
        return f"/zarr/{self.zarr_id}"

    @property
    def dandiset_id(self) -> str:
        """Dandiset ID (read from parent DandisetVersion)."""
        return self.asset.dandiset_id

    @property
    def version_id(self) -> str:
        """Dandiset version ID (read from parent DandisetVersion)."""
        return self.asset.version_id

    def files(
        self,
        prefix: str | None = None,
        after: str | None = None,
        limit: int | None = None,
        download: bool | None = None,
        detail: bool = False,
    ) -> Iterator[dict[str, Any]] | Iterator[str]:
        """Iterator across files in a zarr archive."""
        apipath = self.apipath + "/files"

        params = {}
        if prefix is not None:
            params['prefix'] = prefix
        if after is not None:
            params['after'] = after
        if limit is not None:
            params['limit'] = limit
        if download is not None:
            params['download'] = download

        for file in self._client.paginate(apipath, params=params):
            if detail:
                yield file
            else:
                yield file["Key"]

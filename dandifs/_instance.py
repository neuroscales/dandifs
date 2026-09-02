# NOTICE
#   Some code in this file was copied and modified from [dandi-cli]
#   dandi/consts.py and dandi/utils.py, which are distributed under the
#   Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""
DANDI instance registry and (optional, lazy) discovery.

A :class:`DandiInstance` bundles the GUI and API base URLs of a DANDI-schema
server. A small registry of well-known instances is provided, and arbitrary
(including private / self-hosted) instances can be described by URL. Discovery
of a bare server URL via its ``/info/`` endpoint is available as an *async*
helper and is never performed at import time.
"""
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Union
from urllib.parse import urlsplit

from ._utils import get_logger, joinurl

LOG = get_logger("instance")


@dataclass(frozen=True)
class DandiInstance:
    """Base and GUI URLs of a DANDI instance."""

    name: str
    gui: Optional[str]
    api: str

    def urls(self) -> Iterator[str]:
        """Yield the known URLs (gui, then api) of this instance."""
        if self.gui is not None:
            yield self.gui
        yield self.api


# So it could be easily mapped to an external IP (e.g. from within a VM).
_instancehost = os.environ.get("DANDI_INSTANCEHOST", "localhost")

known_instances: Dict[str, DandiInstance] = {
    "dandi": DandiInstance(
        "dandi",
        "https://dandiarchive.org",
        "https://api.dandiarchive.org/api",
    ),
    "dandi-staging": DandiInstance(
        "dandi-staging",
        "https://gui-staging.dandiarchive.org",
        "https://api-staging.dandiarchive.org/api",
    ),
    "dandi-api-local-docker-tests": DandiInstance(
        "dandi-api-local-docker-tests",
        "http://{}:8085".format(_instancehost),
        "http://{}:8000/api".format(_instancehost),
    ),
    "ember": DandiInstance(
        "ember",
        "https://dandi.emberarchive.org",
        "https://api-dandi.emberarchive.org/api",
    ),
    "ember-sandbox": DandiInstance(
        "ember-sandbox",
        "https://dandi-sandbox.emberarchive.org",
        "https://api-dandi-sandbox.emberarchive.org/api",
    ),
}

#: Reverse mapping (URL -> instance name) for all known URLs.
known_instances_rev: Dict[str, str] = {
    url: name
    for name, inst in known_instances.items()
    for url in inst.urls()
    if url
}


# ----------------------------------------------------------------------
#   Server-info dataclasses (parsed WITHOUT pydantic)
# ----------------------------------------------------------------------


@dataclass
class ServiceURL:
    """A single service URL entry from a server-info response."""

    url: str

    @classmethod
    def _parse(cls, response: dict) -> "ServiceURL":
        return cls(response["url"])


@dataclass
class ServerServices:
    """The ``services`` block of a server-info response."""

    api: ServiceURL
    webui: Optional[ServiceURL] = None
    jupyterhub: Optional[ServiceURL] = None

    @classmethod
    def _parse(cls, response: dict) -> "ServerServices":
        api = ServiceURL._parse(response["api"])
        webui = response.get("webui")
        jupyterhub = response.get("jupyterhub")
        return cls(
            api,
            ServiceURL._parse(webui) if webui else None,
            ServiceURL._parse(jupyterhub) if jupyterhub else None,
        )


@dataclass
class ServerInfo:
    """Parsed representation of a DANDI ``/info/`` response."""

    version: str
    services: ServerServices
    cli_minimal_version: str = ""
    cli_bad_versions: Optional[List[str]] = None

    @classmethod
    def _parse(cls, response: dict) -> "ServerInfo":
        return cls(
            version=response.get("version", ""),
            services=ServerServices._parse(response["services"]),
            cli_minimal_version=response.get(
                "cli_minimal_version",
                response.get("cli-minimal-version", ""),
            ),
            cli_bad_versions=response.get(
                "cli_bad_versions",
                response.get("cli-bad-versions", []),
            ),
        )


def _base_url(url: str) -> str:
    """Return scheme://host[:port] for a URL, dropping path/query/fragment."""
    parts = urlsplit(url)
    return "{}://{}".format(parts.scheme, parts.netloc)


def get_instance(spec: Union[str, DandiInstance]) -> DandiInstance:
    """
    Resolve an instance *without any network I/O*.

    ``spec`` may be:

    - a :class:`DandiInstance` (returned unchanged);
    - the name of a registered instance (e.g. ``"dandi"``);
    - an http(s) URL. Known URLs map to their registered instance; otherwise a
      generic :class:`DandiInstance` is built, treating the URL as the API base.
      Use :func:`discover_instance` to refine an unknown URL via ``/info/``.
    """
    if isinstance(spec, DandiInstance):
        return spec
    if spec.lower().startswith(("http://", "https://")):
        url = spec.rstrip("/")
        # Match the full URL, or its base, against known instances.
        name = known_instances_rev.get(url) or known_instances_rev.get(
            _base_url(url)
        )
        if name is not None:
            return known_instances[name]
        host = urlsplit(url).netloc or url
        return DandiInstance(name=host, gui=None, api=url)
    if spec in known_instances:
        return known_instances[spec]
    raise KeyError(
        "Unknown instance {!r}. Known instances: {}".format(
            spec, ", ".join(sorted(known_instances))
        )
    )


def needs_discovery(instance: DandiInstance) -> bool:
    """True if an instance was built generically from an unknown URL."""
    return instance.name not in known_instances and instance.gui is None


async def discover_instance(session: Any, url: str) -> DandiInstance:
    """
    Discover a DANDI instance from a bare server URL via its ``/info/``
    endpoint (async).

    Tries ``<url>/info/`` first (API base), then ``<url>/server-info`` and
    ``<url>/api/info/`` (GUI base). The response is parsed with
    :class:`ServerInfo` (no pydantic). On any failure, a generic instance
    treating ``url`` as the API base is returned.
    """
    base = url.rstrip("/")
    candidates = (
        joinurl(base, "/info/"),
        joinurl(base, "/server-info"),
        joinurl(base, "/api/info/"),
    )
    info = None
    for candidate in candidates:
        try:
            async with session.get(
                candidate, headers={"Accept": "application/json"}
            ) as resp:
                if resp.status != 200:
                    continue
                info = ServerInfo._parse(await resp.json())
                break
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Discovery request to %s failed: %s", candidate, exc)
            continue
    if info is None:
        LOG.warning("Could not discover instance from %s; using URL as API", url)
        host = urlsplit(base).netloc or base
        return DandiInstance(name=host, gui=None, api=base)
    api_url = info.services.api.url
    host = urlsplit(api_url).netloc or api_url
    return DandiInstance(
        name=host,
        gui=info.services.webui.url if info.services.webui else None,
        api=api_url,
    )

# NOTICE
#   This file was copied and modified from [dandi-cli] dandi/utils.py,
#   which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Dandi Instance."""
# std
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator, List, Optional

# external
import requests
from yarl import URL

# internals
from . import get_logger
from .utils import joinurl

LOG = get_logger()


@dataclass(frozen=True)
class DandiInstance:
    """Representation of a DANDI instance."""

    name: str               # instance name
    gui: str | None         # GUI URL
    api: str                # API URL

    @property
    def redirector(self) -> None:  # noqa: D102
        # For "backwards compatibility"
        return None

    def urls(self) -> Iterator[str]:
        """Yield known URLs (gui, api)."""
        if self.gui is not None:
            yield self.gui
        yield self.api


# So it could be easily mapped to external IP (e.g. from within VM)
# to test against instance running outside of current environment
instancehost = os.environ.get("DANDI_INSTANCEHOST", "localhost")

known_instances = {
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
        f"http://{instancehost}:8085",
        f"http://{instancehost}:8000/api",
    ),
    "linc": DandiInstance(
        "linc",
        "https://lincbrain.org",
        "https://api.lincbrain.org/api",
    ),
    "linc-staging": DandiInstance(
        "linc-staging",
        "https://staging.lincbrain.org",
        "https://staging-api.lincbrain.org/api",
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
# to map back url: name
known_instances_rev = {
    vv: k for k, v in known_instances.items() for vv in v.urls() if vv
}


@dataclass
class ServiceURL:  # noqa: D101
    url: str

    @classmethod
    def _parse(cls, response: dict) -> "ServiceURL":
        return ServiceURL(response["url"])


@dataclass
class ServerServices:  # noqa: D101
    api: ServiceURL
    webui: Optional[ServiceURL] = None
    jupyterhub: Optional[ServiceURL] = None

    @classmethod
    def _parse(cls, response: dict) -> "ServerServices":
        api = ServiceURL._parse(response["api"])
        webui = response.get("webui", None)
        jupyterhub = response.get("jupyterhub", None)
        if webui:
            webui = ServiceURL._parse(webui)
        if jupyterhub:
            jupyterhub = ServiceURL._parse(jupyterhub)
        return ServerServices(api, webui, jupyterhub)


@dataclass
class ServerInfo:  # noqa: D101
    version: str
    services: ServerServices
    cli_minimal_version: str
    cli_bad_versions: List[str]

    @classmethod
    def _parse(cls, response: dict) -> "ServerInfo":
        version = response["version"]
        services = ServerServices._parse(response["services"])
        cli_minimal_version = response.get(
            "cli_minimal_version",
            response.get("cli-minimal-version", "")
        )
        cli_bad_versions = response.get(
            "cli_bad_versions",
            response.get("cli-bad-versions", [])
        )
        return ServerInfo(
            version, services, cli_minimal_version, cli_bad_versions
        )


def get_instance(dandi_instance_id: str | DandiInstance) -> DandiInstance:
    """
    Return an instantiated `DandiInstance` from an instance name or URL.
    """
    dandi_id = None
    is_api = True
    redirector_url = None
    if isinstance(dandi_instance_id, DandiInstance):
        instance = dandi_instance_id
        dandi_id = instance.name
    elif dandi_instance_id.lower().startswith(("http://", "https://")):
        redirector_url = dandi_instance_id.rstrip("/")
        dandi_id = known_instances_rev.get(redirector_url)
        if dandi_id is not None:
            instance = known_instances[dandi_id]
            is_api = instance.api.rstrip("/") == redirector_url
        else:
            instance = None
            is_api = False
            redirector_url = URL(
                redirector_url
            ).with_path("").with_query(None).with_fragment(None)
            redirector_url = str(redirector_url)
    else:
        dandi_id = dandi_instance_id
        instance = known_instances[dandi_id]
    if redirector_url is None:
        assert instance is not None
        redirector_url = instance.api.rstrip("/")
        return _get_instance(redirector_url, True, instance, dandi_id)
    else:
        return _get_instance(redirector_url, is_api, instance, dandi_id)


@lru_cache
def _get_instance(
    url: str,
    is_api: bool,
    instance: DandiInstance | None,
    dandi_id: str | None,
) -> DandiInstance:
    try:
        if is_api:
            r = requests.get(joinurl(url, "/info/"))
        else:
            r = requests.get(joinurl(url, "/server-info"))
            if r.status_code == 404:
                r = requests.get(joinurl(url, "/api/info/"))
        r.raise_for_status()
        server_info = ServerInfo.model_validate(r.json())
    except Exception as e:
        LOG.warning("Request to %s failed (%s)", url, str(e))
        if instance is not None:
            LOG.warning("Using hard-coded URLs")
            return instance
        else:
            raise RuntimeError(
                f"Could not retrieve server info from {url},"
                " and client does not recognize URL"
            )
    api_url = server_info.services.api.url
    if dandi_id is None:
        # Don't use pydantic.AnyHttpUrl, as that sets the `port` attribute even
        # if it's not present in the string.
        u = URL(api_url)
        if u.host is not None:
            dandi_id = u.host
            if (port := u.explicit_port) is not None:
                if ":" in dandi_id:
                    dandi_id = f"[{dandi_id}]"
                dandi_id += f":{port}"
        else:
            dandi_id = api_url
    return DandiInstance(
        name=dandi_id,
        gui=(
            server_info.services.webui.url
            if server_info.services.webui is not None
            else None
        ),
        api=api_url,
    )

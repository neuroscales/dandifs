# NOTICE
#   Some regexes in this file were copied and modified from [dandi-cli]
#   dandi/dandiarchive.py, which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""
Parsing of ``dandi://`` and DANDI archive URLs into a flat
:class:`ParsedDandiURL` structure. Parsing is purely lexical: no network
requests are made and no clients are constructed.

Supported forms
----------------
- ``dandi://<instance>/<dandiset>[@<version>][/<path>]``
- ``DANDI:<dandiset>[/<version>]``
- ``https://<gui>/dandiset/<dandiset>[/<version>][/files[?location=<path>]]``
- ``https://<server>[/api]/dandisets/<dandiset>[/versions[/<version>]]``
  ``[/assets/<asset id>[/download]]``
- ``https://<server>[/api]/dandisets/<dandiset>/versions/<version>/assets/``
  ``?path=<path>`` (or ``?glob=<glob>``)

The ``<path>`` component may descend *into* a Zarr asset; resolving that is the
filesystem's job, not the parser's.
"""
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote

from ._consts import DANDISET_ID_REGEX, VERSION_REGEX
from ._exceptions import UnknownURLError
from ._instance import DandiInstance, get_instance
from ._utils import get_logger

LOG = get_logger("parser")


@dataclass
class ParsedDandiURL:
    """Flat, parsed representation of a DANDI resource identifier."""

    #: The DANDI instance the URL points to.
    instance: DandiInstance
    #: The Dandiset identifier, if any.
    dandiset_id: Optional[str] = None
    #: The version identifier, if explicitly given.
    version_id: Optional[str] = None
    #: A location (path) within the Dandiset, if any. May point into a Zarr.
    path: Optional[str] = None
    #: An asset identifier, if the URL referred to an asset by id.
    asset_id: Optional[str] = None
    #: A glob pattern, if the URL described a glob query.
    glob: Optional[str] = None


_DANDISET_GRP = "(?P<dandiset_id>{})".format(DANDISET_ID_REGEX)
_SERVER_GRP = (
    r"(?P<server>(?P<protocol>https?)://(?P<hostname>[^/]+)/(api/)?)"
)
# Optional version suffix: either ``/versions/<ver>`` or a bare ``/<ver>``.
_VER_SUFFIX = r"(/versions?)?(/(?P<version>{ver}))?".format(ver=VERSION_REGEX)

# List of (regex, kind) pairs, tried in order. ``kind`` selects post-processing.
_KNOWN_URLS = [
    (
        re.compile(
            r"dandi://(?P<instance_name>[-\w._]+)"
            r"/{did}"
            r"(@(?P<version>{ver}))?"
            r"(/(?P<location>.*))?".format(did=_DANDISET_GRP, ver=VERSION_REGEX)
        ),
        "instance",
    ),
    (
        re.compile(
            r"(?P<instance_name>DANDI):{did}(/(?P<version>{ver}))?".format(
                did=_DANDISET_GRP, ver=VERSION_REGEX
            ),
            flags=re.I,
        ),
        "instance",
    ),
    (
        re.compile(
            r"{srv}(#/)?dandisets?/{did}{vsfx}"
            r"/assets/\?path=(?P<path>[^&]+)".format(
                srv=_SERVER_GRP, did=_DANDISET_GRP, vsfx=_VER_SUFFIX
            )
        ),
        "server",
    ),
    (
        re.compile(
            r"{srv}(#/)?dandisets?/{did}{vsfx}"
            r"/assets/\?glob=(?P<glob>[^&]+)".format(
                srv=_SERVER_GRP, did=_DANDISET_GRP, vsfx=_VER_SUFFIX
            )
        ),
        "server",
    ),
    (
        re.compile(
            r"{srv}(#/)?dandisets?/{did}{vsfx}"
            r"/assets/(?P<asset_id>[^?/]+)(/(download/?)?)?".format(
                srv=_SERVER_GRP, did=_DANDISET_GRP, vsfx=_VER_SUFFIX
            )
        ),
        "server",
    ),
    (
        re.compile(
            r"{srv}(#/)?dandisets?/{did}{vsfx}"
            r"(/files(\?location=(?P<location>[^&]*))?)?/?".format(
                srv=_SERVER_GRP, did=_DANDISET_GRP, vsfx=_VER_SUFFIX
            )
        ),
        "server",
    ),
]


def _resolve_instance(kind: str, groups: dict) -> DandiInstance:
    if kind == "instance":
        name = groups["instance_name"].lower()
        try:
            return get_instance(name)
        except KeyError:
            raise UnknownURLError(
                "Unknown instance {!r}".format(groups["instance_name"])
            )
    # kind == "server": build/resolve from the server URL.
    server = groups["server"].rstrip("/")
    return get_instance(server)


def parse_dandi_url(url: str, glob: bool = False) -> ParsedDandiURL:
    """
    Parse a DANDI resource identifier into a :class:`ParsedDandiURL`.

    :param url: the identifier to parse.
    :param glob: if true, a bare location is interpreted as a glob pattern.
    :raises UnknownURLError: if the URL matches no known form.
    """
    LOG.debug("Parsing url %s", url)
    for regex, kind in _KNOWN_URLS:
        match = regex.fullmatch(url)
        if not match:
            continue
        groups = match.groupdict()
        instance = _resolve_instance(kind, groups)
        dandiset_id = groups.get("dandiset_id")
        version_id = groups.get("version")
        asset_id = groups.get("asset_id")
        glob_param = groups.get("glob")
        location = groups.get("location") or groups.get("path")
        if location is not None:
            location = unquote(location).lstrip("/")
        if glob_param:
            return ParsedDandiURL(
                instance, dandiset_id, version_id, glob=unquote(glob_param)
            )
        if asset_id:
            return ParsedDandiURL(
                instance, dandiset_id, version_id, asset_id=asset_id
            )
        if location:
            if glob:
                return ParsedDandiURL(
                    instance, dandiset_id, version_id, glob=location
                )
            return ParsedDandiURL(
                instance, dandiset_id, version_id, path=location
            )
        return ParsedDandiURL(instance, dandiset_id, version_id)
    raise UnknownURLError(
        "Do not know how to parse DANDI URL {!r}".format(url)
    )

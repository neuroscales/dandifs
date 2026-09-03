# NOTICE
#   Some helpers in this file were copied and modified from [dandi-cli]
#   dandi/utils.py, which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Internal utilities for :mod:`dandifs`."""

import logging
import os
import platform
from typing import Optional, Union


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger namespaced under ``dandifs``."""
    return logging.getLogger("dandifs" + ("." + name if name else ""))


def set_logger_level(lgr: logging.Logger, level: Union[int, str]) -> None:
    """Set the level of a logger from an int or a string."""
    if isinstance(level, int):
        pass
    elif str(level).isnumeric():
        level = int(level)
    elif str(level).isalpha():
        level = getattr(logging, str(level).upper(), None)
        if level is None:
            lgr.warning("Do not know how to treat loglevel %s", level)
            return
    else:
        lgr.warning("Do not know how to treat loglevel %s", level)
        return
    lgr.setLevel(level)


LOG = get_logger()
set_logger_level(LOG, os.environ.get("DANDI_LOG_LEVEL", logging.INFO))


def get_version() -> str:
    """Return the installed package version, or a fallback."""
    try:
        from ._version import __version__  # type: ignore

        return __version__
    except Exception:
        return "0+unknown"


USER_AGENT = "dandifs/{} {}/{}".format(
    get_version(),
    platform.python_implementation(),
    platform.python_version(),
)


def joinurl(base: str, path: str) -> str:
    """
    Append a slash-separated ``path`` to a base HTTP(S) URL ``base``.

    The two components are separated by a single slash, collapsing excess
    slashes that would appear after naive concatenation. If ``path`` is
    already an absolute HTTP(S) URL, it is returned unchanged.
    """
    if path.lower().startswith(("http://", "https://")):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def clean_params(params: Optional[dict]) -> Optional[dict]:
    """
    Normalize query parameters for aiohttp: drop ``None`` values and coerce
    booleans and other scalars to strings (aiohttp rejects non-str values).
    """
    if not params:
        return None
    out = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out or None

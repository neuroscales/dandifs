# NOTICE
#   This file was copied and modified from [dandi-cli] dandi/exceptions.py,
#   which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Exceptions."""
from __future__ import annotations

import requests


class UnknownURLError(ValueError):
    """Given url is not known to correspond to DANDI schema(s)."""

    pass


class NotFoundError(RuntimeError):
    """Online resource which we tried to connect to is not found."""

    pass


class FailedToConnectError(RuntimeError):
    """Failed to connect to online resource."""

    pass


class LockingError(RuntimeError):
    """Failed to lock or unlock a resource."""

    pass


class UnknownAssetError(ValueError):  # noqa: D101
    pass


class HTTP404Error(requests.HTTPError):  # noqa: D101
    pass

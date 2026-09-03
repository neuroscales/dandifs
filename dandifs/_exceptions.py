# NOTICE
#   Some names in this file were copied and modified from [dandi-cli]
#   dandi/exceptions.py, which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Internal exceptions for :mod:`dandifs`."""

from typing import Optional


class UnknownURLError(ValueError):
    """The given URL does not correspond to a known DANDI resource."""


class NotFoundError(RuntimeError):
    """An online resource we tried to reach was not found."""


class FailedToConnectError(RuntimeError):
    """Failed to connect to an online resource."""


class DandiHTTPError(RuntimeError):
    """An HTTP request to a DANDI API returned an error status."""

    def __init__(
        self,
        status: int,
        url: str,
        message: Optional[str] = None,
    ) -> None:
        self.status = status
        self.url = url
        self.message = message or ""
        super().__init__(
            "HTTP {} for {}{}".format(
                status, url, ": " + self.message if self.message else ""
            )
        )


class HTTP404Error(DandiHTTPError):
    """An HTTP request returned a 404 (Not Found) status."""

    def __init__(self, url: str, message: Optional[str] = None) -> None:
        super().__init__(404, url, message)

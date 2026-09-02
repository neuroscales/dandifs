# NOTICE
#   Some values in this file were copied and modified from [dandi-cli]
#   dandi/consts.py, which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Internal constants for :mod:`dandifs`."""

#: Regular expression for a valid Dandiset identifier. Not anchored.
DANDISET_ID_REGEX = r"[0-9]{6}"

#: Regular expression for a valid published (non-draft) Dandiset version.
PUBLISHED_VERSION_REGEX = r"[0-9]+\.[0-9]+\.[0-9]+"

#: Regular expression for any valid Dandiset version identifier.
VERSION_REGEX = r"(?:[0-9]+\.[0-9]+\.[0-9]+|draft)"

#: The identifier used for draft Dandiset versions.
DRAFT = "draft"

#: HTTP response status codes that should be retried until retries run out.
RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Number of attempts made for a single request before giving up.
REQUEST_RETRIES = 6

#: File extensions used to identify Zarr assets.
ZARR_EXTENSIONS = (".zarr", ".ngff")

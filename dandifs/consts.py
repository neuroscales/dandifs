# NOTICE
#   This file was copied and modified from [dandi-cli] dandi/consts.py,
#   which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Constants."""
import os
from enum import Enum

#: Regular expression for a valid Dandiset identifier.  This regex is not
#: anchored.
DANDISET_ID_REGEX = r"[0-9]{6}"

#: Regular expression for a valid published (i.e., non-draft) Dandiset version
#: identifier.  This regex is not anchored.
PUBLISHED_VERSION_REGEX = r"[0-9]+\.[0-9]+\.[0-9]+"

#: Regular expression for a valid Dandiset version identifier.  This regex is
#: not anchored.
VERSION_REGEX = rf"(?:{PUBLISHED_VERSION_REGEX}|draft)"


class EmbargoStatus(Enum):  # noqa: D101
    OPEN = "OPEN"
    UNEMBARGOING = "UNEMBARGOING"
    EMBARGOED = "EMBARGOED"


# Download (upload?) specific constants

#: Chunk size when iterating a download (and upload) body.
#: Taken from girder-cli
#: TODO: should we make them smaller for download than for upload?
#: ATM used only in download
MAX_CHUNK_SIZE = int(
    os.environ.get("DANDI_MAX_CHUNK_SIZE", 1024 * 1024 * 8)
)  # 64

#: The identifier for draft Dandiset versions
DRAFT = "draft"

#: HTTP response status codes that should always be retried (until we run out
#: of retries)
RETRY_STATUSES = (429, 500, 502, 503, 504)

VIDEO_FILE_EXTENSIONS = [".mp4", ".avi", ".wmv", ".mov", ".flv", ".mkv"]
VIDEO_FILE_MODULES = ["processing", "acquisition"]

ZARR_EXTENSIONS = [".ngff", ".zarr"]

#: Maximum allowed depth of a Zarr directory tree
MAX_ZARR_DEPTH = 7

#: MIME type assigned to & used to identify Zarr assets
ZARR_MIME_TYPE = "application/x-zarr"

#: Maximum number of Zarr directory entries to upload at once
ZARR_UPLOAD_BATCH_SIZE = 255

#: Maximum number of Zarr directory entries to delete at once
ZARR_DELETE_BATCH_SIZE = 100

BIDS_DATASET_DESCRIPTION = "dataset_description.json"

REQUEST_RETRIES = 12

DOWNLOAD_TIMEOUT = 30

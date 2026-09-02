"""
dandifs: an fsspec filesystem for the DANDI archive.

The only public surface is the filesystem class. Everything else
(``_api``, ``_instance``, ``_parser``, ``_keyring``, ``_utils``, ``_consts``,
``_exceptions``) is internal and may change without notice.
"""
from ._fs import DandiFileSystem
from ._utils import get_logger, get_version

__version__ = get_version()

__all__ = ["DandiFileSystem", "get_logger"]

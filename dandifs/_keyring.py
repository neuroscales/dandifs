# NOTICE
#   Some code in this file was copied and modified from [dandi-cli]
#   dandi/keyring.py, which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""
Lazy, optional credential resolution for :mod:`dandifs`.

Token resolution order (see :func:`resolve_token`):

1. an explicit token argument;
2. the ``DANDI_API_KEY`` environment variable;
3. a per-instance ``<INSTANCE>_API_KEY`` environment variable;
4. the system keyring (only if the optional ``auth`` extra is installed).

The ``keyring`` stack is imported lazily inside ``try``/``except`` so the
library works fully without it.
"""

import os
from typing import Optional

from ._utils import get_logger

LOG = get_logger("keyring")


def _env_token(instance_name: Optional[str]) -> Optional[str]:
    token = os.environ.get("DANDI_API_KEY")
    if token:
        LOG.debug("Using token from DANDI_API_KEY")
        return token
    if instance_name:
        var = "{}_API_KEY".format(
            instance_name.upper().replace("-", "_").replace(".", "_")
        )
        token = os.environ.get(var)
        if token:
            LOG.debug("Using token from %s", var)
            return token
    return None


def _keyring_service(instance_name: Optional[str]) -> str:
    return "dandi-api-{}".format(instance_name or "dandi")


def keyring_lookup(instance_name: Optional[str]) -> Optional[str]:
    """
    Look up a token in the system keyring, or return ``None`` if the keyring
    stack is unavailable or holds no token. Never raises.
    """
    try:
        import keyring  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        LOG.debug("keyring not available (%s); skipping keyring lookup", exc)
        return None
    try:
        return keyring.get_password(_keyring_service(instance_name), "key")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("keyring lookup failed: %s", exc)
        return None


def keyring_save(instance_name: Optional[str], token: str) -> bool:
    """
    Save a token in the system keyring. Returns ``True`` on success, ``False``
    if the keyring stack is unavailable or the save failed. Never raises.
    """
    try:
        import keyring  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        LOG.debug("keyring not available (%s); cannot save token", exc)
        return False
    try:
        keyring.set_password(_keyring_service(instance_name), "key", token)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("keyring save failed: %s", exc)
        return False


def resolve_token(
    instance_name: Optional[str] = None,
    explicit: Optional[str] = None,
    use_keyring: bool = True,
) -> Optional[str]:
    """
    Resolve an API token, or return ``None`` if none is configured.

    The lookup is lazy and side-effect free; the keyring is only consulted if
    it is installed and ``use_keyring`` is true.
    """
    if explicit:
        return explicit
    token = _env_token(instance_name)
    if token:
        return token
    if use_keyring:
        return keyring_lookup(instance_name)
    return None

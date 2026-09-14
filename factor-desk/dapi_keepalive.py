"""DAPI session keepalive for Factor Desk.

Thin helper so ``dapi_enrich.open_dapi_session`` can reuse a live Desktop
session. No credentials. Returns ``None`` when blpapi is absent.
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger("dapi_keepalive")

_SESSION: Any | None = None
REFDATA = "//blp/refdata"


def get_session() -> Any | None:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    try:
        import blpapi  # type: ignore
    except ImportError:
        LOG.info("blpapi not installed — keepalive idle")
        return None
    try:
        sess = blpapi.Session()
        if not sess.start():
            LOG.warning("keepalive start failed")
            return None
        sess.openService(REFDATA)
        _SESSION = sess
        return _SESSION
    except Exception as exc:  # noqa: BLE001
        LOG.warning("keepalive failed: %s", exc)
        return None


def ensure_session() -> Any | None:
    return get_session()


def session() -> Any | None:
    return get_session()

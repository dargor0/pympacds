"""
pympacds — Python Multi Process Asyncio-based Cooperative Discoverable Services Framework.

A lightweight, D-Bus-native application framework for building modular,
multi-process, asyncio-based daemons on Linux.
"""

import logging
import os
from typing import Any, cast

_logger = logging.getLogger(__name__)

_DBUS_BACKEND: str | None = None
_dbus_lib: Any = None
_dbus_aio: Any = None
_dbus_service: Any = None

_import_error_msg: str = (
    "pympacds requires a D-Bus library. "
    "Install either 'dbus-fast' (recommended) or 'dbus-next':\n"
    "    pip install dbus-fast\n"
    "    pip install dbus-next"
)


def _import_dbus() -> tuple[str, Any, Any, Any]:
    """Import the D-Bus library in priority order.

    Tries ``dbus-fast`` first, then ``dbus-next``.  Set the environment
    variable ``PYMPACDS_DBUS_BACKEND`` to ``dbus-fast`` or ``dbus-next``
    to force a specific backend.

    Returns:
        Tuple of (backend_name, dbus_lib_module, dbus_aio_module, dbus_service_module).

    Raises:
        ImportError: If neither library is installed.
    """
    global _DBUS_BACKEND, _dbus_lib, _dbus_aio, _dbus_service

    if _DBUS_BACKEND is not None:
        return _DBUS_BACKEND, _dbus_lib, _dbus_aio, _dbus_service

    if os.environ.get("PYMPACDS_DBUS_BACKEND", "") == "dbus-fast":
        # force dbus-fast
        _try_dbus_fast_import(True)
    elif os.environ.get("PYMPACDS_DBUS_BACKEND", "") == "dbus-next":
        # force dbus-next
        _try_dbus_next_import(True)
    else:
        # try first dbus-fast
        if not _try_dbus_fast_import(False):
            # then dbus-next
            if not _try_dbus_next_import(False):
                # neither, raise error
                raise ImportError(_import_error_msg)

    _logger.info("D-Bus backend: %s", _DBUS_BACKEND)
    return cast(str, _DBUS_BACKEND), _dbus_lib, _dbus_aio, _dbus_service


def _try_dbus_fast_import(doraise: bool) -> bool:
    """Attempt to import ``dbus-fast``.

    Args:
        doraise: If True, raise ImportError instead of returning False.

    Returns:
        True if the import succeeded.
    """
    global _DBUS_BACKEND, _dbus_lib, _dbus_aio, _dbus_service

    try:
        import dbus_fast as _dbus_lib
        import dbus_fast.aio.message_bus as _dbus_aio
        import dbus_fast.service as _dbus_service

        _DBUS_BACKEND = "dbus-fast"
    except ImportError:
        if doraise:
            raise ImportError(_import_error_msg) from None
        return False
    else:
        return True


def _try_dbus_next_import(doraise: bool) -> bool:
    """Attempt to import ``dbus-next``.

    Args:
        doraise: If True, raise ImportError instead of returning False.

    Returns:
        True if the import succeeded.
    """
    global _DBUS_BACKEND, _dbus_lib, _dbus_aio, _dbus_service

    try:
        import dbus_next as _dbus_lib
        import dbus_next.aio.message_bus as _dbus_aio
        import dbus_next.service as _dbus_service

        _DBUS_BACKEND = "dbus-next"
    except ImportError:
        if doraise:
            raise ImportError(_import_error_msg) from None
        return False
    else:
        return True


def get_dbus_backend() -> str:
    """Return the active D-Bus library name (``"dbus-fast"`` or ``"dbus-next"``)."""
    if _DBUS_BACKEND is None:
        _import_dbus()
    return cast(str, _DBUS_BACKEND)


def get_dbus_lib() -> Any:
    """Return the active D-Bus library root module."""
    if _dbus_lib is None:
        _import_dbus()
    return _dbus_lib


def get_dbus_aio() -> Any:
    """Return the active D-Bus asyncio module."""
    if _dbus_aio is None:
        _import_dbus()
    return _dbus_aio


def get_dbus_service() -> Any:
    """Return the active D-Bus service module."""
    if _dbus_service is None:
        _import_dbus()
    return _dbus_service

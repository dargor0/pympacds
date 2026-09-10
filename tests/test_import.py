"""Tests for D-Bus library import strategy (REQ-DBUS-001), import error path and edge cases."""

import os
import sys

import pytest


class PrepareModule:
    """Context manager that temporarily removes D-Bus libraries from sys.modules
    and optionally blocks their import.

    Args:
        monkeypatch: pytest ``monkeypatch`` fixture.
        blockimports: If True, raise ImportError on any D-Bus library import.
        force_env: If set, override ``PYMPACDS_DBUS_BACKEND`` during the block.
    """

    def __init__(self, monkeypatch, blockimports: bool = False, force_env: str | None = None):
        self.mp = monkeypatch
        self.bi = blockimports
        self.fenv = force_env
        self.envval: str | None = None

    def __enter__(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("dbus_fast") or key.startswith("dbus_next"):
                self.mp.delitem(sys.modules, key, raising=False)

        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):
            if self.bi and (name.startswith("dbus_fast") or name.startswith("dbus_next")):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        self.mp.setattr(builtins, "__import__", mock_import)

        if self.fenv is not None:
            self.envval = os.environ.get("PYMPACDS_DBUS_BACKEND", None)
            os.environ["PYMPACDS_DBUS_BACKEND"] = self.fenv

    def __exit__(self, exc_type, exc_value, tb) -> None:
        self.mp.undo()

        if self.fenv is not None:
            if self.envval is None:
                os.environ.pop("PYMPACDS_DBUS_BACKEND")
            else:
                os.environ["PYMPACDS_DBUS_BACKEND"] = self.envval


def test_get_dbus_backend(monkeypatch):
    """get_dbus_backend returns a recognised backend name."""
    with PrepareModule(monkeypatch, False):
        from pympacds import get_dbus_backend

        backend = get_dbus_backend()
        assert backend in ("dbus-fast", "dbus-next")


def test_get_dbus_lib(monkeypatch):
    """get_dbus_lib returns a non-None module object."""
    with PrepareModule(monkeypatch, False):
        from pympacds import get_dbus_lib

        lib = get_dbus_lib()
        assert lib is not None


def test_get_dbus_aio(monkeypatch):
    """get_dbus_aio returns a non-None module object."""
    with PrepareModule(monkeypatch, False):
        from pympacds import get_dbus_aio

        aio = get_dbus_aio()
        assert aio is not None


def test_get_dbus_service(monkeypatch):
    """get_dbus_service returns a non-None module object."""
    with PrepareModule(monkeypatch, False):
        from pympacds import get_dbus_service

        svc = get_dbus_service()
        assert svc is not None


def test_all_modules_match(monkeypatch):
    """All getter functions return dbus-fast modules when dbus-fast is installed."""
    with PrepareModule(monkeypatch, False):
        from pympacds import (
            get_dbus_aio,
            get_dbus_backend,
            get_dbus_lib,
            get_dbus_service,
        )

        backend = get_dbus_backend()
        assert backend == "dbus-fast"
        assert get_dbus_lib().__name__ == "dbus_fast"
        assert get_dbus_aio().__name__.startswith("dbus_fast")
        assert get_dbus_service().__name__.startswith("dbus_fast")


def test_import_error_message(monkeypatch):
    """ImportError is raised with a clear message when no D-Bus library is installed."""
    with PrepareModule(monkeypatch, True):
        import pympacds

        pympacds._DBUS_BACKEND = None
        with pytest.raises(ImportError, match="requires a D-Bus library"):
            pympacds._import_dbus()


def test_import_force_dbus_next(monkeypatch):
    """PYMPACDS_DBUS_BACKEND=dbus-next forces the dbus-next backend."""
    with PrepareModule(monkeypatch, False, "dbus-next"):
        import pympacds
        from pympacds import (
            get_dbus_aio,
            get_dbus_backend,
            get_dbus_lib,
            get_dbus_service,
        )

        pympacds._DBUS_BACKEND = None
        backend = get_dbus_backend()
        assert backend == "dbus-next"
        assert get_dbus_lib().__name__ == "dbus_next"
        assert get_dbus_aio().__name__.startswith("dbus_next")
        assert get_dbus_service().__name__.startswith("dbus_next")


def test_import_force_dbus_fast(monkeypatch):
    """PYMPACDS_DBUS_BACKEND=dbus-fast forces the dbus-fast backend."""
    with PrepareModule(monkeypatch, False, "dbus-fast"):
        import pympacds
        from pympacds import (
            get_dbus_aio,
            get_dbus_backend,
            get_dbus_lib,
            get_dbus_service,
        )

        pympacds._DBUS_BACKEND = None
        backend = get_dbus_backend()
        assert backend == "dbus-fast"
        assert get_dbus_lib().__name__ == "dbus_fast"
        assert get_dbus_aio().__name__.startswith("dbus_fast")
        assert get_dbus_service().__name__.startswith("dbus_fast")


def test_import_force_dbus_unknown(monkeypatch):
    """PYMPACDS_DBUS_BACKEND with an unknown value falls back to dbus-fast."""
    with PrepareModule(monkeypatch, False, "dbus-unknown"):
        import pympacds
        from pympacds import (
            get_dbus_aio,
            get_dbus_backend,
            get_dbus_lib,
            get_dbus_service,
        )

        pympacds._DBUS_BACKEND = None
        backend = get_dbus_backend()
        assert backend == "dbus-fast"
        assert get_dbus_lib().__name__ == "dbus_fast"
        assert get_dbus_aio().__name__.startswith("dbus_fast")
        assert get_dbus_service().__name__.startswith("dbus_fast")

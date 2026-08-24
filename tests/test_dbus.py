"""Unit tests for DBusManager (no real bus required)."""

import asyncio
import logging
import pytest

from pympacds.dbus import DBusManager


class FakeDBusIf:
    """Mock of the org.freedesktop.DBus proxy used by update_friendbus."""

    def __init__(self, names):
        self._names = names
        self.list_names_calls = 0

    async def call_list_names(self):
        self.list_names_calls += 1
        return list(self._names)


def _make_manager(bus_prefix="org.pympacds.test"):
    logger = logging.getLogger("test_dbus_unit")
    logger.setLevel(logging.WARN)
    return DBusManager(
        logger=logger,
        busname="svc",
        bus_prefix=bus_prefix,
        discovery_enabled=False,
    )


class TestUpdateFriendbusReconciles:
    @pytest.mark.asyncio
    async def test_adds_new_names(self):
        """Names from ListNames sharing the prefix are added."""
        mgr = _make_manager()
        mgr._dbusif = FakeDBusIf(["org.pympacds.test.svc", "org.pympacds.test.sensor"])

        await mgr.update_friendbus()

        assert mgr.friendbus == {"org.pympacds.test.svc", "org.pympacds.test.sensor"}

    @pytest.mark.asyncio
    async def test_removes_stale_names(self):
        """Names that disappeared from the bus are removed (the reconcile bug)."""
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc", "org.pympacds.test.stale"}
        mgr._dbusif = FakeDBusIf(["org.pympacds.test.svc"])

        await mgr.update_friendbus()

        assert mgr.friendbus == {"org.pympacds.test.svc"}
        assert "org.pympacds.test.stale" not in mgr.friendbus

    @pytest.mark.asyncio
    async def test_ignores_other_prefixes(self):
        """Names outside the prefix are not added."""
        mgr = _make_manager()
        mgr._dbusif = FakeDBusIf(
            ["org.pympacds.test.svc", "com.example.other", "org.freedesktop.DBus"]
        )

        await mgr.update_friendbus()

        assert mgr.friendbus == {"org.pympacds.test.svc"}

    @pytest.mark.asyncio
    async def test_signals_change_event(self):
        """friendchanges is set when the set changes."""
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc"}
        mgr._dbusif = FakeDBusIf(["org.pympacds.test.svc", "org.pympacds.test.sensor"])
        mgr.friendchanges.clear()

        await mgr.update_friendbus()

        assert mgr.friendchanges.is_set()

    @pytest.mark.asyncio
    async def test_no_signal_when_unchanged(self):
        """friendchanges is not set when the set is already up to date."""
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc"}
        mgr._dbusif = FakeDBusIf(["org.pympacds.test.svc"])
        mgr.friendchanges.clear()

        await mgr.update_friendbus()

        assert not mgr.friendchanges.is_set()

    @pytest.mark.asyncio
    async def test_list_names_exception_logged_not_raised(self):
        """An exception from ListNames is logged, not propagated."""
        class FailingDBusIf:
            async def call_list_names(self):
                raise RuntimeError("boom")

        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc"}
        mgr._dbusif = FailingDBusIf()

        await mgr.update_friendbus()  # must not raise

        # friend set is left untouched on failure
        assert mgr.friendbus == {"org.pympacds.test.svc"}

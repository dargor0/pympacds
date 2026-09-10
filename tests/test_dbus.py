"""Unit tests for DBusManager (no real bus required)."""

import asyncio
import logging
import queue
import pytest

from pympacds.dbus import DBusManager


class _FakeNode:
    """Stand-in for a D-Bus library introspection node."""

    def __init__(self, xml):
        self._xml = xml

    def tostring(self):
        return self._xml


class _FakeProxy:
    """Stand-in for a cached D-Bus proxy object."""

    def get_interface(self, ifname):
        return "proxy:" + ifname


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


@pytest.fixture(autouse=True)
def _use_fake_backend(fake_dbus_backend):
    """Route every test in this module through the fake D-Bus backend."""
    yield


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


class _StopBus:
    """Fake bus exposing a configurable ``_writer`` for stop() drain tests."""

    def __init__(self, writer):
        self._writer = writer
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True

    async def wait_for_disconnect(self):
        pass


class TestBusnamePrefixing:
    @pytest.mark.asyncio
    async def test_busname_without_prefix_gets_prefixed(self):
        mgr = _make_manager(bus_prefix="org.pympacds.test")
        assert mgr.busname == "org.pympacds.test.svc"

    @pytest.mark.asyncio
    async def test_busname_already_prefixed(self):
        logger = logging.getLogger("test_dbus_prefix")
        logger.setLevel(logging.WARN)
        mgr = DBusManager(
            logger=logger,
            busname="org.pympacds.test.svc",
            bus_prefix="org.pympacds.test",
            discovery_enabled=False,
        )
        assert mgr.busname == "org.pympacds.test.svc"


class TestPathResolution:
    @pytest.mark.asyncio
    async def test_absolute_path_returned_unchanged(self):
        mgr = _make_manager()
        assert mgr._resolve_path("/already/absolute") == "/already/absolute"

    @pytest.mark.asyncio
    async def test_relative_path_expanded(self):
        mgr = _make_manager()
        assert mgr._resolve_path("rel") == f"{mgr._obj_root}/rel"


class TestAddInterface:
    @pytest.mark.asyncio
    async def test_same_path_appends_second_interface(self):
        class FakeIface:
            def __init__(self, name):
                self.name = name

        mgr = _make_manager()
        mgr.add_interface("svc", FakeIface("iface1"))
        mgr.add_interface("svc", FakeIface("iface2"))
        assert [i.name for i in mgr.ifacelist[f"{mgr._obj_root}/svc"]] == ["iface1", "iface2"]


class TestStopDrain:
    @pytest.mark.asyncio
    async def test_stop_drains_list_writer(self):
        class ListWriter:
            def __init__(self):
                self.messages = []

        fb = _StopBus(ListWriter())
        mgr = _make_manager()
        mgr._drain_timeout_ms = 2
        mgr.bus = fb
        await mgr.stop()
        assert fb.disconnected

    @pytest.mark.asyncio
    async def test_stop_drains_queue_writer(self):
        class QueueWriter:
            def __init__(self):
                self.messages = queue.Queue()

        fb = _StopBus(QueueWriter())
        mgr = _make_manager()
        mgr._drain_timeout_ms = 2
        mgr.bus = fb
        await mgr.stop()
        assert fb.disconnected


class TestGetInterface:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_introspection(self):
        mgr = _make_manager()
        mgr._proxy_cache[("svc.bus", "/obj")] = _FakeProxy()
        result = await mgr.get_interface("svc.bus", "/obj", "com.example.I")
        assert result == "proxy:com.example.I"


class TestIntrospect:
    @pytest.mark.asyncio
    async def test_introspect_returns_snapshot(self):
        class FakeBus:
            async def introspect(self, busname, pathname):
                return _FakeNode('<node><interface name="com.example.I"/></node>')

        mgr = _make_manager()
        mgr.bus = FakeBus()
        snap = await mgr.introspect("svc.bus", "/obj")
        assert snap.path() == "/obj"
        assert "com.example.I" in snap.interface_names()


class TestIntrospectTree:
    @pytest.mark.asyncio
    async def test_walks_children_skips_errors_and_caches(self):
        class TreeBus:
            def __init__(self):
                self.calls = []

            async def introspect(self, busname, pathname):
                self.calls.append(pathname)
                if pathname == "/root":
                    return _FakeNode('<node><node name="a"/><node name="b"/></node>')
                if pathname == "/root/a":
                    raise RuntimeError("boom")
                return _FakeNode("<node/>")

        mgr = _make_manager()
        mgr.bus = TreeBus()
        tree = await mgr.introspect_tree("svc.bus", "/root")
        assert set(tree) == {"/root", "/root/b"}
        assert "/root/a" in mgr.bus.calls

        cached = await mgr.introspect_tree("svc.bus", "/root")
        assert cached is tree


class TestFriendBusDefaults:
    @pytest.mark.asyncio
    async def test_get_friend_bus_default_ifname_and_objpath(self):
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test-svc"}
        captured = {}

        async def fake_get_interface(busname, pathname, ifname):
            captured["busname"] = busname
            captured["pathname"] = pathname
            captured["ifname"] = ifname
            return "proxy"

        mgr.get_interface = fake_get_interface
        result = await mgr.get_friend_bus("org.pympacds.test-svc")
        assert result == "proxy"
        assert captured["ifname"] == "org.pympacds.test"
        assert captured["pathname"] == mgr._obj_root


class TestSignalHandlers:
    @pytest.mark.asyncio
    async def test_acquired_non_matching_prefix(self):
        mgr = _make_manager()
        mgr._signal_name_acquired("com.example.other")
        assert mgr.friendbus == set()

    @pytest.mark.asyncio
    async def test_lost_non_matching_prefix(self):
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc"}
        mgr._signal_name_lost("com.example.other")
        assert mgr.friendbus == {"org.pympacds.test.svc"}

    @pytest.mark.asyncio
    async def test_owner_changed_non_matching_prefix(self):
        mgr = _make_manager()
        mgr._signal_name_owner_changed("com.example.other", "o1", "o2")
        assert mgr.friendbus == set()

    @pytest.mark.asyncio
    async def test_lost_matching_prefix_invalidates_caches(self):
        mgr = _make_manager()
        mgr.friendbus = {"org.pympacds.test.svc"}
        mgr._introspect_cache = {"org.pympacds.test.svc": {"x": 1}}
        mgr._proxy_cache = {("org.pympacds.test.svc", "/p"): "proxy"}
        mgr._signal_name_lost("org.pympacds.test.svc")
        assert mgr.friendbus == set()
        assert "org.pympacds.test.svc" not in mgr._introspect_cache
        assert ("org.pympacds.test.svc", "/p") not in mgr._proxy_cache

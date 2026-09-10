"""Integration tests for DBusManager with a real bus."""

import asyncio
import logging

import pytest

pytestmark = pytest.mark.asyncio


async def test_bus_name_assignment(session_bus):
    """Bus name is assigned correctly and uses the prefix."""
    assert session_bus.busname.startswith("org.pympacds.test")
    assert session_bus.bus.unique_name is not None
    assert session_bus.bus.unique_name.startswith(":")


async def test_friend_bus_scan(session_bus):
    """update_friendbus scans for peers sharing the prefix."""
    await session_bus.update_friendbus()
    assert session_bus.busname in session_bus.friendbus


async def test_is_friend_busname(session_bus):
    """Membership check works."""
    await session_bus.update_friendbus()
    assert session_bus.is_friend_busname(session_bus.busname) is True
    assert session_bus.is_friend_busname("com.nonexistent") is False


async def test_query_friend_busname(session_bus):
    """Query returns matching friend names."""
    await session_bus.update_friendbus()
    all_friends = session_bus.query_friend_busname()
    assert session_bus.busname in all_friends
    matches = session_bus.query_friend_busname("fixture")
    assert session_bus.busname in matches


async def test_get_interface_introspects_dbus(session_bus):
    """get_interface can introspect org.freedesktop.DBus."""
    dbus_if = await session_bus.get_interface(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus"
    )
    assert dbus_if is not None
    names = await dbus_if.call_list_names()
    assert isinstance(names, list)
    assert "org.freedesktop.DBus" in names


async def test_get_friend_bus(session_bus, session_bus_address):
    """get_friend_bus returns a proxy for a discovered friend."""
    from pympacds.contracts import ServiceContract, dbus_method
    from pympacds.dbus import DBusManager

    # define a minimal contract the friend exports
    class FriendContract(ServiceContract):
        iface_name = "FriendTest"

        def __init__(self, ifname, base):
            super().__init__(ifname, base)
            self._require("dbus_friend_ping")

        @dbus_method()
        def ping(self) -> "b":
            return self.base.dbus_friend_ping()

    class FriendBase:
        def dbus_friend_ping(self):
            return True

    # spawn a second service ("friend") on the same bus
    friend_logger = logging.getLogger("test_friend")
    friend_logger.setLevel(logging.WARN)
    friend = DBusManager(
        logger=friend_logger,
        busname="friend.svc",
        bus_prefix="org.pympacds.test",
        discovery_enabled=False,
        name_flags=0,
    )
    iface = FriendContract("org.pympacds.test.FriendTest", FriendBase())
    friend.add_interface("friend", iface)
    await friend.start()
    try:
        # the fixture service discovers the friend
        await session_bus.update_friendbus()
        assert friend.busname in session_bus.friendbus

        # get a proxy to the friend's interface
        proxy = await session_bus.get_friend_bus(
            friend.busname,
            ifname="org.pympacds.test.FriendTest",
            objpath=friend._obj_root + "/friend",
        )
        assert proxy is not None
    finally:
        await friend.stop()


async def test_get_friend_bus_invalid_raises(session_bus):
    """get_friend_bus raises ValueError for unknown bus names."""
    with pytest.raises(ValueError, match="not available"):
        await session_bus.get_friend_bus("com.nonexistent.bus")


async def test_wait_friend_changes_event(session_bus):
    """wait_friend_changes can be awaited and signaled."""
    await session_bus.update_friendbus()

    async def trigger():
        await asyncio.sleep(0.05)
        session_bus.friendchanges.set()

    asyncio.create_task(trigger())
    await session_bus.wait_friend_changes()

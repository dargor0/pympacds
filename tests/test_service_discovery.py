"""Tests for service_discovery.py (REQ-DISC)."""

import asyncio
import pytest


class MockDBusManager:
    def __init__(self):
        self.friendbus = {"org.pympacds.test_svc_a", "org.pympacds.test_svc_b"}
        self._update_calls = 0

    async def update_friendbus(self):
        self._update_calls += 1

    def query_friend_busname(self, query=""):
        if not query:
            return list(self.friendbus)
        return [n for n in self.friendbus if query in n]

    async def get_friend_bus(self, busname, ifname=None, objpath=None):
        return f"proxy_{busname}"


class MockLogger:
    def debug(self, msg, *args): pass
    def warning(self, msg, *args): pass
    def exception(self, msg, *args): pass


@pytest.mark.asyncio
async def test_connect_friendbus_new_friend():
    from pympacds.service_discovery import connect_friendbus

    busobj = MockDBusManager()
    logger = MockLogger()
    busdict = {}
    callbacks = []

    def cb(busname, busif, mcb):
        callbacks.append((busname, mcb))
        return f"cb_{busname}"

    await connect_friendbus("test_svc", busdict, cb, busobj, logger)

    assert len(busdict) == 2
    assert "org.pympacds.test_svc_a" in busdict
    assert "org.pympacds.test_svc_b" in busdict
    assert callbacks[0][1] is None  # first call: mcb=None
    assert busobj._update_calls == 1


@pytest.mark.asyncio
async def test_connect_friendbus_removed_friend():
    from pympacds.service_discovery import connect_friendbus

    busobj = MockDBusManager()
    logger = MockLogger()
    busdict = {
        "org.pympacds.stale_svc": {
            "busif": "proxy_stale_svc", "mcb": "cb_stale"
        }
    }
    callbacks = []

    def cb(busname, busif, mcb):
        callbacks.append((busname, mcb))
        return f"cb_{busname}"

    await connect_friendbus("test_svc", busdict, cb, busobj, logger)

    assert "org.pympacds.stale_svc" not in busdict
    # unsubscribe called with the stored mcb
    assert ("org.pympacds.stale_svc", "cb_stale") in callbacks


@pytest.mark.asyncio
async def test_connect_friendbus_no_callback():
    from pympacds.service_discovery import connect_friendbus

    busobj = MockDBusManager()
    logger = MockLogger()
    busdict = {}

    await connect_friendbus("test_svc", busdict, None, busobj, logger)

    assert len(busdict) == 2
    assert busdict["org.pympacds.test_svc_a"]["mcb"] is None

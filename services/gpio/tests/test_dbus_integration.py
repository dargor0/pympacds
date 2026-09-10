"""Black-box D-Bus integration tests (REQ-GPIO-009, REQ-SVC-009)."""

import asyncio
import configparser
import json
import logging
import os

import pytest
from helpers import FakeShim
from pympacds.dbus import DBusManager

import pympacds_gpio.service as svc_mod

logger = logging.getLogger("test_dbus_integration")
logger.setLevel(logging.CRITICAL)


def _write_config(tmp_path):
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
    cp["dbus"] = {
        "bus_prefix": "org.pympacds",
        "contract_health": "true",
        "contract_config": "false",
    }
    cp["gpio"] = {
        "chip": "",
        "lines": "0",
        "watch_lines": "1",
        "edge": "both",
        "debounce_us": "0",
        "active_low": "false",
        "output_lines": "2",
        "default_values": "2=1",
    }
    path = tmp_path / "gpio.ini"
    with open(path, "w") as f:
        cp.write(f)
    return str(path)


@pytest.mark.asyncio
async def test_lines_status_and_values_over_dbus(session_bus_address, tmp_path):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    fake_shim = FakeShim(values={0: False, 1: False, 2: True})
    svc = svc_mod.GpioService(shim_factory=lambda: fake_shim)
    svc.logger.setLevel(logging.CRITICAL)
    cfg = _write_config(tmp_path)
    assert svc.setup(["-c", cfg]) is True
    assert await svc.start_dbus() is True

    try:
        client = DBusManager(
            logger=logger,
            busname="testclient",
            bus_prefix="org.pympacds",
            discovery_enabled=False,
        )
        await client.start()

        proxy = await client.get_interface(
            "org.pympacds.gpio", "/org/pympacds/gpio", "org.pympacds.GPIO"
        )

        lines = json.loads(await proxy.call_get_lines())
        assert lines == {"0": False, "1": False, "2": True}

        status = json.loads(await proxy.call_status())
        assert status["chip"] == "/dev/gpiochip0"
        assert status["output_lines"] == ["2"]
        assert status["watch_lines"] == ["1"]

        assert await proxy.call_get_value("0") is False
        assert await proxy.call_set_value("2", False) is True
        assert await proxy.call_get_value("2") is False

        assert await proxy.call_get_default_value("2") is True
        assert await proxy.call_set_default_value("2", False) is True
        assert await proxy.call_get_default_value("2") is False

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)


@pytest.mark.asyncio
async def test_line_changed_signal_over_dbus(session_bus_address, tmp_path):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    fake_shim = FakeShim(values={0: False, 1: False, 2: False})
    svc = svc_mod.GpioService(shim_factory=lambda: fake_shim)
    svc.logger.setLevel(logging.CRITICAL)
    cfg = _write_config(tmp_path)
    assert svc.setup(["-c", cfg]) is True
    assert await svc.start_dbus() is True

    try:
        client = DBusManager(
            logger=logger,
            busname="testclient2",
            bus_prefix="org.pympacds",
            discovery_enabled=False,
        )
        await client.start()

        proxy = await client.get_interface(
            "org.pympacds.gpio", "/org/pympacds/gpio", "org.pympacds.GPIO"
        )

        changed = []

        def on_changed(name, offset, value):
            changed.append((name, offset, value))

        proxy.on_line_changed(on_changed)

        fake_shim._values[1] = True
        fake_shim._events.append((1, True))

        for _ in range(200):
            await asyncio.sleep(0.01)
            if changed:
                break

        assert changed == [("", 1, True)]

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)

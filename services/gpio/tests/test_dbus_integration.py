"""Black-box D-Bus integration tests (REQ-GPIO-009, REQ-SVC-009).

Black-box (REQ-XCUT-002.1): the service runs as an external process and is
exercised only over D-Bus.  GPIO needs a real chip, so this test is **opt-in**:
it runs only when ``MPACDS_GPIO_CHIP`` (a chip path) or ``MPACDS_CMD_GPIO`` (a
C++ service override) is set — set ``MPACDS_BUS_PREFIX`` for the C++ port.

(In-process tests that inject a fake shim live in ``test_service_integration.py``.)
"""

import asyncio
import configparser
import logging
import os
import signal
import subprocess
import sys
import time

import pytest
from pympacds.dbus import DBusManager

logger = logging.getLogger("test_dbus_integration")
logger.setLevel(logging.CRITICAL)

_DEFAULT_ARGV = [sys.executable, "-m", "pympacds_gpio.service"]
_CHIP = os.environ.get("MPACDS_GPIO_CHIP")
_RUN = bool(_CHIP or os.environ.get("MPACDS_CMD_GPIO"))


def _service_output(proc):
    """Stop the service (if alive) and return its (stdout, stderr)."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        return proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.communicate()


def _write_config(tmp_path, bus_prefix):
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
    cp["dbus"] = {
        "bus_prefix": bus_prefix,
        "contract_health": "true",
        "contract_config": "false",
        "discovery_enabled": "false",
    }
    cp["gpio"] = {
        "chip": _CHIP or "",
        "lines": "0",
        "watch_lines": "",
        "edge": "both",
        "debounce_us": "0",
        "active_low": "false",
        "output_lines": "",
        "default_values": "",
    }
    path = tmp_path / "gpio.ini"
    with open(path, "w") as f:
        cp.write(f)
    return str(path)


def _objpath(bus_prefix, leaf):
    return "/" + bus_prefix.replace(".", "/") + "/" + leaf


async def _wait_for_bus_name(client, name, timeout=10.0):
    dbus = await client.get_interface(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name in await dbus.call_list_names():
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.mark.skipif(not _RUN, reason="needs a GPIO chip: set MPACDS_GPIO_CHIP")
@pytest.mark.asyncio
async def test_contract_surface_over_dbus(
    session_bus_address, tmp_path, launch_service, bus_prefix
):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    cfg = _write_config(tmp_path, bus_prefix)
    proc = launch_service("gpio", cfg, session_bus_address, _DEFAULT_ARGV)
    client = DBusManager(
        logger=logger,
        busname="testclient",
        bus_prefix=bus_prefix,
        discovery_enabled=False,
    )
    try:
        await client.start()
        if not await _wait_for_bus_name(client, f"{bus_prefix}.gpio"):
            out, err = _service_output(proc)
            pytest.fail(
                f"service never registered {bus_prefix}.gpio (rc={proc.returncode})\n"
                f"--- service stderr ---\n{err}\n--- service stdout ---\n{out}"
            )

        health = await client.get_interface(
            f"{bus_prefix}.gpio", _objpath(bus_prefix, "health"), f"{bus_prefix}.Health"
        )
        assert await health.call_ping() is True

        proxy = await client.get_interface(
            f"{bus_prefix}.gpio", _objpath(bus_prefix, "gpio"), f"{bus_prefix}.GPIO"
        )
        assert isinstance(await proxy.call_status(), str)

        await client.stop()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)

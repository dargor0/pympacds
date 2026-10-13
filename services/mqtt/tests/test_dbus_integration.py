"""Black-box D-Bus integration tests (REQ-MQTT-008, REQ-SVC-009).

Black-box (REQ-XCUT-002.1): the service runs as an external process and is
exercised only over D-Bus.  No broker is required — the tests assert the
always-available contract surface, so the same tests run against the C++ port
by setting ``MPACDS_CMD_MQTT`` and ``MPACDS_BUS_PREFIX``.

(In-process tests that inject a fake paho client live in
``test_service_integration.py``.)
"""

import asyncio
import configparser
import json
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

_DEFAULT_ARGV = [sys.executable, "-m", "pympacds_mqtt.service"]


def _service_output(proc):
    """Stop the service (if alive) and return its (stdout, stderr)."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        return proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.communicate()


def _write_config(tmp_path, bus_prefix, **mqtt_kv):
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
    cp["dbus"] = {
        "bus_prefix": bus_prefix,
        "contract_health": "true",
        "contract_config": "false",
        "discovery_enabled": "false",
    }
    cp["mqtt"] = {
        "host": "127.0.0.1",
        "port": "1",  # closed port: connect fails fast, service stays up
        "connect_timeout_s": "1",
        "reconnect_min_s": "1",
        "reconnect_max_s": "1",
        **mqtt_kv,
    }
    path = tmp_path / "mqtt.ini"
    with open(path, "w") as f:
        cp.write(f)
    return str(path)


def _objpath(bus_prefix, leaf):
    return "/" + bus_prefix.replace(".", "/") + "/" + leaf


async def _wait_for_bus_name(client, name, timeout=10.0):
    """Poll ListNames until ``name`` appears (service startup is asynchronous)."""
    dbus = await client.get_interface(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name in await dbus.call_list_names():
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.mark.asyncio
async def test_contract_surface_over_dbus(
    session_bus_address, tmp_path, launch_service, bus_prefix
):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    cfg = _write_config(tmp_path, bus_prefix)
    proc = launch_service("mqtt", cfg, session_bus_address, _DEFAULT_ARGV)
    client = DBusManager(
        logger=logger,
        busname="testclient",
        bus_prefix=bus_prefix,
        discovery_enabled=False,
    )
    try:
        await client.start()
        if not await _wait_for_bus_name(client, f"{bus_prefix}.mqtt"):
            out, err = _service_output(proc)
            pytest.fail(
                f"service never registered {bus_prefix}.mqtt (rc={proc.returncode})\n"
                f"--- service stderr ---\n{err}\n--- service stdout ---\n{out}"
            )

        proxy = await client.get_interface(
            f"{bus_prefix}.mqtt", _objpath(bus_prefix, "mqtt"), f"{bus_prefix}.MQTT"
        )

        # status() returns a JSON snapshot; with no broker the client is down
        status = json.loads(await proxy.call_status())
        assert status["connected"] is False

        # send() without a default topic must fail
        assert await proxy.call_send("payload") is False

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

"""Black-box D-Bus integration tests (REQ-HTTP-007, REQ-SVC-009).

Black-box (REQ-XCUT-002.1): the service runs as an external process and is
exercised only over D-Bus.  The target URL is unreachable, so the test asserts
the fire-and-forget contract (a token is returned and a ``request_completed``
signal carrying an error is emitted).  The same tests run against the C++ port
by setting ``MPACDS_CMD_HTTP`` and ``MPACDS_BUS_PREFIX``.

(In-process tests that inject a fake aiohttp session live in
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

_DEFAULT_ARGV = [sys.executable, "-m", "pympacds_http.service"]


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
    cp["http"] = {
        "timeout_s": "1",
        "retries": "0",
        "retry_backoff_s": "0",
        "tls_verify": "true",
        "max_redirects": "5",
        "default_url": "http://127.0.0.1:1/",  # closed port: request error
        "default_method": "POST",
        "default_headers": "",
    }
    path = tmp_path / "http.ini"
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


async def _wait_completed(completed, token, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for tok, result in completed:
            if tok == token:
                return result
        await asyncio.sleep(0.05)
    return None


@pytest.mark.asyncio
async def test_fire_and_forget_contract_over_dbus(
    session_bus_address, tmp_path, launch_service, bus_prefix
):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    cfg = _write_config(tmp_path, bus_prefix)
    proc = launch_service("http", cfg, session_bus_address, _DEFAULT_ARGV)
    client = DBusManager(
        logger=logger,
        busname="testclient",
        bus_prefix=bus_prefix,
        discovery_enabled=False,
    )
    try:
        await client.start()
        if not await _wait_for_bus_name(client, f"{bus_prefix}.http"):
            out, err = _service_output(proc)
            pytest.fail(
                f"service never registered {bus_prefix}.http (rc={proc.returncode})\n"
                f"--- service stderr ---\n{err}\n--- service stdout ---\n{out}"
            )

        proxy = await client.get_interface(
            f"{bus_prefix}.http", _objpath(bus_prefix, "http"), f"{bus_prefix}.HTTP"
        )

        completed = []

        def on_completed(token, result):
            completed.append((token, result))

        proxy.on_request_completed(on_completed)

        token = await proxy.call_send("payload")
        assert len(token) == 32

        result_json = await _wait_completed(completed, token)
        assert result_json is not None, "request_completed signal not received"
        result = json.loads(result_json)
        assert result["error"] is not None  # unreachable target

        # Health contract is exposed too (at the /health path)
        health = await client.get_interface(
            f"{bus_prefix}.http", _objpath(bus_prefix, "health"), f"{bus_prefix}.Health"
        )
        assert await health.call_ping() is True

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

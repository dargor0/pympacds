"""Black-box D-Bus integration tests (REQ-HTTP-007, REQ-SVC-009)."""

import asyncio
import configparser
import json
import logging
import os

import pytest
from helpers import FakeResponse, FakeSession
from pympacds.dbus import DBusManager

import pympacds_http.service as svc_mod

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
    cp["http"] = {
        "timeout_s": "10",
        "retries": "0",
        "retry_backoff_s": "0",
        "tls_verify": "true",
        "max_redirects": "5",
        "default_url": "http://example.test/",
        "default_method": "POST",
        "default_headers": "",
    }
    path = tmp_path / "http.ini"
    with open(path, "w") as f:
        cp.write(f)
    return str(path)


async def _wait_completed(completed, token, timeout=2.0):
    for _ in range(int(timeout * 100)):
        for tok, result in completed:
            if tok == token:
                return result
        await asyncio.sleep(0.01)
    return None


@pytest.mark.asyncio
async def test_send_returns_token_and_emits_completed(session_bus_address, tmp_path):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    fake_session = FakeSession([FakeResponse(200, b"hello world")])
    svc = svc_mod.HttpService(session_factory=lambda s: fake_session)
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
            "org.pympacds.http", "/org/pympacds/http", "org.pympacds.HTTP"
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
        assert result["status_code"] == 200
        assert result["body"] == "hello world"
        assert result["error"] is None

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)


@pytest.mark.asyncio
async def test_download_saves_file_and_emits_completed(session_bus_address, tmp_path):
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    fake_session = FakeSession([FakeResponse(200, b"\x00\x01\x02binary")])
    svc = svc_mod.HttpService(session_factory=lambda s: fake_session)
    svc.logger.setLevel(logging.CRITICAL)
    cfg = _write_config(tmp_path)
    assert svc.setup(["-c", cfg]) is True
    assert await svc.start_dbus() is True

    dest = tmp_path / "downloaded.bin"

    try:
        client = DBusManager(
            logger=logger,
            busname="testclient2",
            bus_prefix="org.pympacds",
            discovery_enabled=False,
        )
        await client.start()

        proxy = await client.get_interface(
            "org.pympacds.http", "/org/pympacds/http", "org.pympacds.HTTP"
        )

        completed = []

        def on_completed(token, result):
            completed.append((token, result))

        proxy.on_request_completed(on_completed)

        token = await proxy.call_download("payload", str(dest))
        assert len(token) == 32

        result_json = await _wait_completed(completed, token)
        assert result_json is not None, "request_completed signal not received"

        result = json.loads(result_json)
        assert result["download_path"] == str(dest)
        assert result["bytes_written"] == 9
        assert result["body"] is None
        assert dest.read_bytes() == b"\x00\x01\x02binary"

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)

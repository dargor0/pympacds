"""In-process D-Bus integration tests (REQ-MQTT-008, REQ-SVC-009).

These build the Python service directly and inject a fake paho client, so they
are Python-only and are **not** part of the reusable black-box suite
(REQ-XCUT-002.1). See ``test_dbus_integration.py`` for the black-box tests.
"""

import configparser
import json
import logging
import os

import pytest
from pympacds.dbus import DBusManager

import pympacds_mqtt.service as svc_mod

logger = logging.getLogger("test_dbus_integration")
logger.setLevel(logging.CRITICAL)


class FakeShim:
    """Injected shim with a mocked broker for black-box tests."""

    def __init__(self, config, logger):
        self.config = config
        self._connected = True
        self.published = []
        self.subscribed = []

    @property
    def connected(self):
        return self._connected

    async def connect(self):
        return self._connected

    async def reconnect(self):
        return self._connected

    def subscribe(self, topic, qos=None):
        self.subscribed.append(topic)
        return True

    def unsubscribe(self, topic):
        return True

    def publish(self, topic, payload, qos=None):
        self.published.append((topic, payload))
        return True

    def status(self):
        return {
            "client_id": "test",
            "connected": self._connected,
            "broker_ip": "192.0.2.10",
            "broker_port": 1883,
            "protocol_version": "3.1.1",
            "tls": False,
            "subscribed_topics": sorted(self.subscribed),
            "reconnect_attempts": 0,
            "connected_time": "2026-09-10T00:00:00",
            "disconnected_time": None,
        }

    async def stop(self):
        pass


def _objpath(bus_prefix, leaf):
    return "/" + bus_prefix.replace(".", "/") + "/" + leaf


def _write_config(tmp_path, bus_prefix, **mqtt_kv):
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
    cp["dbus"] = {
        "bus_prefix": bus_prefix,
        "contract_health": "true",
        "contract_config": "false",
        "discovery_enabled": "false",
    }
    cp["mqtt"] = {"host": "localhost", "port": "1883", "qos": "1", **mqtt_kv}
    path = tmp_path / "mqtt.ini"
    with open(path, "w") as f:
        cp.write(f)
    return str(path)


@pytest.mark.asyncio
async def test_publish_and_status_over_dbus(session_bus_address, tmp_path, monkeypatch, bus_prefix):
    monkeypatch.setattr(svc_mod, "MqttShim", FakeShim)
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    svc = svc_mod.MqttService()
    svc.logger.setLevel(logging.CRITICAL)
    cfg = _write_config(tmp_path, bus_prefix)
    assert svc.setup(["-c", cfg]) is True
    assert await svc.start_dbus() is True

    try:
        client = DBusManager(
            logger=logger,
            busname="testclient",
            bus_prefix=bus_prefix,
            discovery_enabled=False,
        )
        await client.start()

        proxy = await client.get_interface(
            f"{bus_prefix}.mqtt", _objpath(bus_prefix, "mqtt"), f"{bus_prefix}.MQTT"
        )

        # publish returns True and reaches the fake broker
        assert await proxy.call_publish("topic/1", "hello") is True

        # status() returns a JSON snapshot
        status = json.loads(await proxy.call_status())
        assert status["connected"] is True
        assert status["broker_ip"] == "192.0.2.10"

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)


@pytest.mark.asyncio
async def test_send_fails_without_default_topic(
    session_bus_address, tmp_path, monkeypatch, bus_prefix
):
    monkeypatch.setattr(svc_mod, "MqttShim", FakeShim)
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    svc = svc_mod.MqttService()
    svc.logger.setLevel(logging.CRITICAL)
    cfg = _write_config(tmp_path, bus_prefix)  # default_topic empty
    assert svc.setup(["-c", cfg]) is True
    assert await svc.start_dbus() is True

    try:
        client = DBusManager(
            logger=logger,
            busname="testclient2",
            bus_prefix=bus_prefix,
            discovery_enabled=False,
        )
        await client.start()

        proxy = await client.get_interface(
            f"{bus_prefix}.mqtt", _objpath(bus_prefix, "mqtt"), f"{bus_prefix}.MQTT"
        )
        assert await proxy.call_send("payload") is False  # no default_topic

        await client.stop()
    finally:
        await svc.bus.stop()
        if oldaddr:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
        else:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)

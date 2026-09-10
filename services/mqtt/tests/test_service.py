"""Service-level unit tests (REQ-MQTT-008)."""

import asyncio
import json
import logging

import pytest
from helpers import make_config

from pympacds_mqtt.service import MqttService
from pympacds_mqtt.shim import MqttShim

logger = logging.getLogger("test_service")
logger.setLevel(logging.CRITICAL)


def _service(fake_client, **cfg):
    svc = MqttService()
    svc.logger.setLevel(logging.CRITICAL)
    svc.config["mqtt"] = make_config(**cfg)
    svc.shim = MqttShim(make_config(**cfg), svc.logger, client_factory=lambda: fake_client)
    return svc


class TestSend:
    def test_send_without_default_topic(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_mqtt_send("payload") is False

    def test_send_requires_connection(self, fake_client):
        svc = _service(fake_client, default_topic="out/topic")
        assert svc.dbus_mqtt_send("payload") is False  # not connected


class TestStatus:
    def test_status_returns_json(self, fake_client):
        svc = _service(fake_client)
        data = json.loads(svc.dbus_mqtt_status())
        assert data["connected"] is False
        assert data["protocol_version"] == "3.1.1"

    def test_connected_delegates(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_mqtt_connected() is False


class TestConfig:
    def test_get_set(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_config_set("mqtt", "host", "newhost") is True
        assert svc.dbus_config_get("mqtt", "host") == "newhost"

    def test_get_missing_section(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_config_get("nope", "key") == ""

    def test_set_creates_section(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_config_set("newsection", "key", "value") is True
        assert svc.dbus_config_get("newsection", "key") == "value"


class TestTopics:
    def test_subscribe_topics_parsing(self, fake_client):
        svc = _service(fake_client)
        svc.config["mqtt"]["subscribe_topics"] = " a/b , c/d ,, "
        assert svc._subscribe_topics() == ["a/b", "c/d"]

    def test_subscribe_topics_empty(self, fake_client):
        svc = _service(fake_client)
        assert svc._subscribe_topics() == []


class TestHealth:
    def test_ping(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_health_ping() is True

    def test_status_includes_tags(self, fake_client):
        svc = _service(fake_client)
        data = json.loads(svc.dbus_health_status())
        assert "provides" in data
        assert "requires" in data


class TestDbusFlag:
    def test_default_when_no_section(self, fake_client):
        svc = _service(fake_client)
        assert svc._dbus_flag("contract_health", True) is True

    def test_from_config(self, fake_client):
        svc = _service(fake_client)
        svc.config["dbus"] = {"contract_config": "true"}
        assert svc._dbus_flag("contract_config", False) is True

    def test_invalid_value(self, fake_client):
        svc = _service(fake_client)
        svc.config["dbus"] = {"contract_config": "maybe"}
        assert svc._dbus_flag("contract_config", False) is False


class TestDelegation:
    def test_dbus_mqtt_publish(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_mqtt_publish("t", "p") is False  # not connected

    def test_dbus_mqtt_subscribe(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_mqtt_subscribe("t") is False

    def test_dbus_mqtt_unsubscribe(self, fake_client):
        svc = _service(fake_client)
        assert svc.dbus_mqtt_unsubscribe("t") is False

    @pytest.mark.asyncio
    async def test_dbus_mqtt_reconnect(self, fake_client):
        svc = _service(fake_client, connect_timeout_s="0.01")
        assert await svc.dbus_mqtt_reconnect() is False


class TestTasks:
    @pytest.mark.asyncio
    async def test_update_tasks_creates_tasks(self, fake_client):
        svc = _service(fake_client)
        svc.update_tasks()
        assert "mqtt_loop" in svc.tasklist
        assert "mqtt_messages" in svc.tasklist
        for t in svc.tasklist.values():
            t.cancel()
        await asyncio.gather(*svc.tasklist.values(), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_consume_messages(self, fake_client):
        svc = _service(fake_client)

        class FakeContract:
            def __init__(self):
                self.received = []

            def message_received(self, topic, payload):
                self.received.append((topic, payload))

        svc._contract = FakeContract()
        svc.shim._ensure_client()
        svc.shim._messages.put_nowait(("t", b"p"))
        task = asyncio.create_task(svc._consume_messages())
        await asyncio.sleep(0.01)
        task.cancel()
        await task
        assert svc._contract.received == [("t", b"p")]


class TestCloseLoop:
    @pytest.mark.asyncio
    async def test_close_loop(self, fake_client):
        svc = _service(fake_client)
        await svc.close_loop()


class TestConfigChanged:
    def test_config_set_emits_changed(self, fake_client):
        svc = _service(fake_client)

        class FakeContract:
            def __init__(self):
                self.changed = []

            def config_changed(self, section, key, value):
                self.changed.append((section, key, value))

        svc._config_contract = FakeContract()
        assert svc.dbus_config_set("mqtt", "host", "newhost") is True
        assert svc._config_contract.changed == [("mqtt", "host", "newhost")]


class TestStartDbus:
    @pytest.mark.asyncio
    async def test_start_dbus_wires_contracts(self, monkeypatch):
        import pympacds_mqtt.service as svc_mod

        calls = {}

        class FakeBus:
            def __init__(self, **kwargs):
                calls["bus"] = kwargs

            def add_interface(self, path, iface):
                calls.setdefault("interfaces", []).append(path)

            async def start(self):
                calls["started"] = True

        class FakeShim:
            def __init__(self, config, logger):
                calls["shim_config"] = dict(config)

            async def connect(self):
                calls["connected"] = True
                return True

            def subscribe(self, topic):
                calls.setdefault("subscribed", []).append(topic)

        class FakeContract:
            def __init__(self, ifname, base):
                calls["contract"] = ifname

        class FakeHealth:
            def __init__(self, ifname, base):
                calls["health"] = ifname

        class FakeConfig:
            def __init__(self, ifname, base):
                calls["config"] = ifname

        monkeypatch.setattr(svc_mod, "DBusManager", FakeBus)
        monkeypatch.setattr(svc_mod, "MqttShim", FakeShim)
        monkeypatch.setattr(svc_mod, "MqttContract", FakeContract)
        monkeypatch.setattr(svc_mod, "HealthContract", FakeHealth)
        monkeypatch.setattr(svc_mod, "ConfigContract", FakeConfig)

        svc = MqttService()
        svc.logger.setLevel(logging.CRITICAL)
        svc.config["mqtt"] = make_config(subscribe_topics="a, b")
        svc.config["dbus"] = {"contract_health": "true", "contract_config": "true"}

        assert await svc.start_dbus() is True
        assert calls["started"] is True
        assert calls["connected"] is True
        assert calls["subscribed"] == ["a", "b"]
        assert calls["contract"] == f"{svc.bus_prefix}.MQTT"
        assert calls["health"] == f"{svc.bus_prefix}.Health"
        assert calls["config"] == f"{svc.bus_prefix}.Config"

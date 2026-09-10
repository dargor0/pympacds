"""Contract unit tests (REQ-MQTT-004)."""

import pytest

from pympacds_mqtt.contracts import MqttContract


class FakeBase:
    def __init__(self):
        self.calls = []

    def dbus_mqtt_publish(self, topic, payload):
        return True

    def dbus_mqtt_send(self, payload):
        return True

    def dbus_mqtt_subscribe(self, topic):
        return True

    def dbus_mqtt_unsubscribe(self, topic):
        return True

    async def dbus_mqtt_reconnect(self):
        return True

    def dbus_mqtt_status(self):
        return "{}"

    def dbus_mqtt_connected(self):
        self.calls.append(("connected",))
        return True


@pytest.fixture
def contract():
    base = FakeBase()
    return MqttContract("org.pympacds.MQTT", base), base


def test_contract_construction_requires_callbacks():
    class EmptyBase:
        pass

    with pytest.raises(AttributeError):
        MqttContract("org.pympacds.MQTT", EmptyBase())


def test_message_received_signal_returns_args(contract):
    c, _ = contract
    assert c.message_received("t", "p") == ["t", "p"]


def test_connected_property_delegates(contract):
    c, base = contract
    assert c.connected is True
    assert base.calls == [("connected",)]

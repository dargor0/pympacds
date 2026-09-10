"""MQTT D-Bus contract (REQ-MQTT-004)."""

from pympacds import get_dbus_lib
from pympacds.contracts import ServiceContract, dbus_method, dbus_property, dbus_signal

_PropertyAccess = get_dbus_lib().PropertyAccess  # type: ignore[attr-defined]


class MqttContract(ServiceContract):
    """MQTT publish/subscribe interface."""

    iface_name = "MQTT"
    iface_version = "1.0.0"
    iface_provides = ["mqtt", "publish-subscribe"]
    iface_requires = ["network"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_mqtt_publish",
            "dbus_mqtt_send",
            "dbus_mqtt_subscribe",
            "dbus_mqtt_unsubscribe",
            "dbus_mqtt_reconnect",
            "dbus_mqtt_status",
            "dbus_mqtt_connected",
        )

    @dbus_method()
    def publish(self, topic: "s", payload: "s") -> "b":
        return self.base.dbus_mqtt_publish(topic, payload)

    @dbus_method()
    def send(self, payload: "s") -> "b":
        return self.base.dbus_mqtt_send(payload)

    @dbus_method()
    def subscribe(self, topic: "s") -> "b":
        return self.base.dbus_mqtt_subscribe(topic)

    @dbus_method()
    def unsubscribe(self, topic: "s") -> "b":
        return self.base.dbus_mqtt_unsubscribe(topic)

    @dbus_method()
    async def reconnect(self) -> "b":
        return await self.base.dbus_mqtt_reconnect()

    @dbus_method()
    def status(self) -> "s":
        return self.base.dbus_mqtt_status()

    @dbus_signal()
    def message_received(self, topic: "s", payload: "s") -> "ss":
        return [topic, payload]

    @dbus_property(access=_PropertyAccess.READ)
    def connected(self) -> "b":
        return self.base.dbus_mqtt_connected()

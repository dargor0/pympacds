"""MQTT client service (REQ-MQTT-001..009)."""

import asyncio
import configparser
import json
import time
from typing import Any

from pympacds.builtin_contracts import ConfigContract, HealthContract
from pympacds.dbus import DBusManager
from pympacds.process import ProcessBase

from .contracts import MqttContract
from .shim import MqttShim


class MqttService(ProcessBase):
    """Maintains an MQTT connection and exposes publish/subscribe over D-Bus."""

    def __init__(self):
        super().__init__(
            name="mqtt",
            version="0.1.0",
            description="MQTT client service for pympacds",
        )
        self.bus: Any = None  # DBusManager, created in start_dbus()
        self._start_time = time.time()
        self.shim = None
        self._contract = None
        self._health_contract = None
        self._config_contract = None

    # -- D-Bus setup ----------------------------------------------------

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="mqtt",
            bus_prefix=self.bus_prefix,
        )
        self.shim = MqttShim(self.config["mqtt"], self.logger)

        self._contract = MqttContract(f"{self.bus_prefix}.MQTT", self)
        self.bus.add_interface("mqtt", self._contract)

        if self._dbus_flag("contract_health", True):
            self._health_contract = HealthContract(f"{self.bus_prefix}.Health", self)
            self.bus.add_interface("health", self._health_contract)

        if self._dbus_flag("contract_config", False):
            self._config_contract = ConfigContract(f"{self.bus_prefix}.Config", self)
            self.bus.add_interface("config", self._config_contract)

        await self.bus.start()

        connected = await self.shim.connect()
        if connected:
            for topic in self._subscribe_topics():
                self.shim.subscribe(topic)
        else:
            self.logger.warning("mqtt: initial connection failed; will keep retrying")
        return True

    def _dbus_flag(self, key: str, default: bool) -> bool:
        if not self.config.has_section("dbus"):
            return default
        try:
            return self.config["dbus"].getboolean(key, default)
        except ValueError:
            return default

    def _subscribe_topics(self) -> list[str]:
        raw = self.config["mqtt"].get("subscribe_topics", "")
        return [t.strip() for t in raw.split(",") if t.strip()]

    # -- tasks (REQ-CORE-010) ------------------------------------------

    def update_tasks(self) -> None:
        if "mqtt_loop" not in self.tasklist:
            self.tasklist["mqtt_loop"] = asyncio.create_task(self.shim.run_loop(), name="mqtt_loop")
        if "mqtt_messages" not in self.tasklist:
            self.tasklist["mqtt_messages"] = asyncio.create_task(
                self._consume_messages(), name="mqtt_messages"
            )

    async def _consume_messages(self) -> None:
        try:
            async for topic, payload in self.shim.messages():
                if self._contract is not None:
                    try:
                        self._contract.message_received(topic, payload)
                    except Exception:  # noqa: BLE001
                        pass
        except asyncio.CancelledError:
            pass

    # -- graceful shutdown (REQ-MQTT-007) ------------------------------

    async def close_loop(self) -> None:
        if self.shim is not None:
            await self.shim.stop()
        await super().close_loop()

    # -- MQTT contract base callbacks (REQ-MQTT-004) -------------------

    def dbus_mqtt_publish(self, topic: str, payload: str) -> bool:
        return self.shim.publish(topic, payload)

    def dbus_mqtt_send(self, payload: str) -> bool:
        topic = self.config["mqtt"].get("default_topic", "").strip()
        if not topic:
            return False
        return self.shim.publish(topic, payload)

    def dbus_mqtt_subscribe(self, topic: str) -> bool:
        return self.shim.subscribe(topic)

    def dbus_mqtt_unsubscribe(self, topic: str) -> bool:
        return self.shim.unsubscribe(topic)

    async def dbus_mqtt_reconnect(self) -> bool:
        return await self.shim.reconnect()

    def dbus_mqtt_status(self) -> str:
        return json.dumps(self.shim.status())

    def dbus_mqtt_connected(self) -> bool:
        return self.shim.connected

    # -- health / config contract base callbacks (REQ-XCUT-004) --------

    def dbus_health_ping(self) -> bool:
        return True

    def dbus_health_status(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "uptime": self.dbus_health_get_uptime(),
                "provides": self.dbus_health_get_provides(),
                "requires": self.dbus_health_get_requires(),
            }
        )

    def dbus_health_get_uptime(self) -> int:
        return int(time.time() - self._start_time)

    def dbus_config_get(self, section: str, key: str) -> str:
        try:
            return self.config[section][key]
        except (KeyError, configparser.Error):
            return ""

    def dbus_config_set(self, section: str, key: str, value: str) -> bool:
        try:
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config[section][key] = value
        except Exception:  # noqa: BLE001
            return False
        if self._config_contract is not None:
            try:
                self._config_contract.config_changed(section, key, value)
            except Exception:  # noqa: BLE001
                pass
        return True


def main() -> None:
    MqttService().start()


if __name__ == "__main__":
    main()

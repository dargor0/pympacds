"""collector — example pympacds service that discovers sensors via friendbus.

Run:  python3 collector.py -c collector.ini
"""

import asyncio
from pympacds import get_dbus_lib
from pympacds.process import ProcessBase
from pympacds.dbus import DBusManager
from pympacds.contracts import ServiceContract, dbus_property

_PropertyAccess = get_dbus_lib().PropertyAccess

_SENSOR_IFACE = "org.pympacds.example.Sensor"
_SENSOR_REL_PATH = "sensor"


class CollectorContract(ServiceContract):
    iface_name = "Collector"

    def __init__(self, ifname, base):
        super().__init__(ifname, base)
        self._require("dbus_collector_get_last_value")

    @dbus_property(access=_PropertyAccess.READ)
    def last_value(self) -> "i":
        return self.base.dbus_collector_get_last_value()


class CollectorService(ProcessBase):
    def __init__(self):
        super().__init__(
            name="collector",
            version="0.1.0",
            description="Example collector service for pympacds",
        )
        self._last_value = 0
        self._sensors: dict[str, object] = {}

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="collector",
            bus_prefix=self.bus_prefix,
        )
        self.iface = CollectorContract("org.pympacds.example.Collector", self)
        self.bus.add_interface("collector", self.iface)
        await self.bus.start()
        self.logger.info("Collector started: bus=%s", self.bus.busname)
        return True

    def update_tasks(self) -> None:
        if "scanner" not in self.tasklist:
            self.tasklist["scanner"] = asyncio.create_task(
                self._scan_friends(), name="scanner"
            )

    def dbus_collector_get_last_value(self) -> int:
        return self._last_value

    def _on_measurement(self, uid: str, value: int) -> None:
        self._last_value = value
        self.logger.info(
            "Received measurement: sensor_uid=%s value=%d", uid, value
        )

    async def _scan_friends(self) -> None:
        """Periodically discover sensors sharing the bus prefix and subscribe
        to their measurement signal, unsubscribing when they disappear."""
        try:
            while not self.exitevent.is_set():
                await self.bus.update_friendbus()
                sensor_buses = self.bus.query_friend_busname("sensor")

                # subscribe to new sensors
                for busname in sensor_buses:
                    if busname not in self._sensors:
                        try:
                            proxy = await self.bus.get_friend_bus(
                                busname,
                                ifname=_SENSOR_IFACE,
                                objpath=f"{self.bus._obj_root}/{_SENSOR_REL_PATH}",
                            )
                            proxy.on_measurement(self._on_measurement)
                            self._sensors[busname] = proxy
                            self.logger.info("Subscribed to sensor: %s", busname)
                        except Exception:
                            self.logger.exception(
                                "Unable to subscribe to sensor %s", busname
                            )

                # unsubscribe from departed sensors
                for busname in list(self._sensors):
                    if busname not in sensor_buses:
                        del self._sensors[busname]
                        self.logger.info("Unsubscribed from sensor: %s", busname)

                await self.do_waitexit(timeout=5)
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    CollectorService().start()

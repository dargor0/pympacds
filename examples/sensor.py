"""sensor — example pympacds service that emits periodic measurements.

Run:  python3 sensor.py -c sensor.ini
"""

import asyncio
import json
import random
import uuid

from pympacds import get_dbus_lib
from pympacds.contracts import ServiceContract, dbus_method, dbus_property, dbus_signal
from pympacds.dbus import DBusManager
from pympacds.process import ProcessBase

_PropertyAccess = get_dbus_lib().PropertyAccess


class SensorContract(ServiceContract):
    iface_name = "Sensor"

    def __init__(self, ifname, base):
        super().__init__(ifname, base)
        self._require("dbus_sensor_identify", "dbus_sensor_get_id")

    @dbus_method()
    def identify(self) -> "s":
        return self.base.dbus_sensor_identify()

    @dbus_signal()
    def measurement(self, uid: "s", value: "i") -> "si":
        return [uid, value]

    @dbus_property(access=_PropertyAccess.READ)
    def sensor_id(self) -> "s":
        return self.base.dbus_sensor_get_id()


class SensorService(ProcessBase):
    def __init__(self):
        super().__init__(
            name="sensor",
            version="0.1.0",
            description="Example sensor service for pympacds",
        )
        self._sensor_id = str(uuid.uuid4())[:8]
        self._value = 0

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="sensor",
            bus_prefix=self.bus_prefix,
        )
        self.iface = SensorContract("org.pympacds.example.Sensor", self)
        self.bus.add_interface("sensor", self.iface)
        await self.bus.start()
        self.logger.info("Sensor started: bus=%s id=%s", self.bus.busname, self._sensor_id)
        return True

    def update_tasks(self) -> None:
        if "emitter" not in self.tasklist:
            self.tasklist["emitter"] = asyncio.create_task(self._emit_periodic(), name="emitter")

    def dbus_sensor_identify(self) -> str:
        return json.dumps(
            {
                "bus": self.bus.busname,
                "id": self._sensor_id,
                "pid": self.pid,
            }
        )

    def dbus_sensor_get_id(self) -> str:
        return self._sensor_id

    async def _emit_periodic(self) -> None:
        period = self.config["sensor"].getint("period_s", fallback=2)
        try:
            while not self.exitevent.is_set():
                await self.do_waitexit(timeout=period)
                self._value = random.randint(1, 100)
                uid = str(uuid.uuid4())[:8]
                self.iface.measurement(uid, self._value)
                self.logger.info("Emitted measurement: uid=%s value=%d", uid, self._value)
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    SensorService().start()

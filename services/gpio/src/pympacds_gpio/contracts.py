"""GPIO D-Bus contract (REQ-GPIO-005)."""

from pympacds import get_dbus_lib
from pympacds.contracts import ServiceContract, dbus_method, dbus_property, dbus_signal

_PropertyAccess = get_dbus_lib().PropertyAccess  # type: ignore[attr-defined]


class GpioContract(ServiceContract):
    """GPIO read/write/watch interface."""

    iface_name = "GPIO"
    iface_version = "1.0.0"
    iface_provides = ["gpio"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_gpio_get_value",
            "dbus_gpio_set_value",
            "dbus_gpio_get_default_value",
            "dbus_gpio_set_default_value",
            "dbus_gpio_get_lines",
            "dbus_gpio_status",
            "dbus_gpio_watched_lines",
        )

    @dbus_method()
    def get_value(self, line: "s") -> "b":
        return self.base.dbus_gpio_get_value(line)

    @dbus_method()
    def set_value(self, line: "s", value: "b") -> "b":
        return self.base.dbus_gpio_set_value(line, value)

    @dbus_method()
    def get_default_value(self, line: "s") -> "b":
        return self.base.dbus_gpio_get_default_value(line)

    @dbus_method()
    def set_default_value(self, line: "s", value: "b") -> "b":
        return self.base.dbus_gpio_set_default_value(line, value)

    @dbus_method()
    def get_lines(self) -> "s":
        return self.base.dbus_gpio_get_lines()

    @dbus_method()
    def status(self) -> "s":
        return self.base.dbus_gpio_status()

    @dbus_signal()
    def line_changed(self, name: "s", offset: "u", value: "b") -> "sub":
        return [name, offset, value]

    @dbus_property(access=_PropertyAccess.READ)
    def watched_lines(self) -> "as":
        return self.base.dbus_gpio_watched_lines()

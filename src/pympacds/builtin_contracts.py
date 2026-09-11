"""Built-in D-Bus interface contracts.

- HealthContract (enabled by default)
- LifecycleContract, ConfigContract, MetricsContract (opt-in via [dbus] INI)
"""

from . import get_dbus_lib
from .contracts import ServiceContract, dbus_method, dbus_property, dbus_signal

_PropertyAccess = get_dbus_lib().PropertyAccess


class HealthContract(ServiceContract):
    """Service health and diagnostics.

    Exported at the "health" relative object path automatically
    when [dbus] contract_health = true (default).
    """

    iface_name = "Health"
    iface_version = "1.0.0"
    iface_provides = ["health"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_health_ping",
            "dbus_health_status",
            "dbus_health_get_uptime",
            "dbus_health_get_provides",
            "dbus_health_get_requires",
        )

    @dbus_method()
    def ping(self) -> "b":
        return self.base.dbus_health_ping()

    @dbus_method()
    def status(self) -> "s":
        return self.base.dbus_health_status()

    @dbus_signal()
    def heartbeat(self, uptime: "i") -> "i":
        return [uptime]

    @dbus_property(access=_PropertyAccess.READ)
    def uptime(self) -> "u":
        return self.base.dbus_health_get_uptime()

    @dbus_property(access=_PropertyAccess.READ)
    def provides(self) -> "as":
        return self.base.dbus_health_get_provides()

    @dbus_property(access=_PropertyAccess.READ)
    def requires(self) -> "as":
        return self.base.dbus_health_get_requires()


class LifecycleContract(ServiceContract):
    """Remote restart and shutdown.

    Exported when [dbus] contract_lifecycle = true.
    """

    iface_name = "Lifecycle"
    iface_version = "1.0.0"
    iface_provides = ["lifecycle"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_lifecycle_restart",
            "dbus_lifecycle_shutdown",
        )

    @dbus_method()
    def restart(self) -> "b":
        return self.base.dbus_lifecycle_restart()

    @dbus_method()
    def shutdown(self) -> "b":
        return self.base.dbus_lifecycle_shutdown()

    @dbus_signal()
    def state_changed(self, new_state: "s") -> "s":
        return [new_state]


class ConfigContract(ServiceContract):
    """Runtime configuration management.

    Exported when [dbus] contract_config = true.
    """

    iface_name = "Config"
    iface_version = "1.0.0"
    iface_provides = ["config"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_config_get",
            "dbus_config_set",
        )

    @dbus_method()
    def get_config(self, section: "s", key: "s") -> "s":
        return self.base.dbus_config_get(section, key)

    @dbus_method()
    def set_config(self, section: "s", key: "s", value: "s") -> "b":
        return self.base.dbus_config_set(section, key, value)

    @dbus_signal()
    def config_changed(self, section: "s", key: "s", value: "s") -> "sss":
        return [section, key, value]


class MetricsContract(ServiceContract):
    """Operational metrics exposition.

    Exported when [dbus] contract_metrics = true.
    """

    iface_name = "Metrics"
    iface_version = "1.0.0"
    iface_provides = ["metrics"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require("dbus_metrics_get")

    @dbus_method()
    def get_metrics(self) -> "s":
        return self.base.dbus_metrics_get()

    @dbus_signal()
    def metric_update(self, name: "s", value: "v") -> "sv":
        return [name, value]

# Built-in Contracts

pympacds ships four standard D-Bus interface contracts. Each is a `ServiceContract` subclass that:

- declares its D-Bus interface name, version, and a category tag,
- delegates every method, property, and signal to `self.base` (the service),
- validates at construction time that the service implements the required callback methods, raising `AttributeError` otherwise.

Contracts are enabled via boolean keys in the `[dbus]` section of the INI file:

```ini
[dbus]
contract_health = true
contract_metrics = false
contract_lifecycle = false
contract_config = false
```

| Contract | Default | Purpose |
|----------|---------|---------|
| `HealthContract` | `true` | Health and diagnostics |
| `LifecycleContract` | `false` | Remote restart and shutdown |
| `ConfigContract` | `false` | Runtime configuration management |
| `MetricsContract` | `false` | Operational metrics |

Only the INI file (or environment override) controls these; they have no constructor parameter.

---

## HealthContract

Exported at the `"health"` relative object path. Enabled by default.

| Member | Type | Description |
|--------|------|-------------|
| `ping()` | method | Returns `true` if the service is responsive |
| `status()` | method | Returns a JSON status string |
| `heartbeat(uptime)` | signal | Emitted periodically with uptime in seconds |
| `uptime` | property (u) | Seconds since the service started |

The `heartbeat` signal is emitted automatically by the framework at an interval set by `[dbus] heartbeat_interval_s` (default 30, range 5–3600).

**Required callbacks on the service:**

- `dbus_health_ping() -> bool`
- `dbus_health_status() -> str`
- `dbus_health_get_uptime() -> int`

---

## LifecycleContract

Exposes remote restart and shutdown. Enabled by `contract_lifecycle = true`.

| Member | Type | Description |
|--------|------|-------------|
| `restart()` | method | Triggers a graceful restart via the shutdown path |
| `shutdown()` | method | Triggers a graceful shutdown |
| `state_changed(state)` | signal | Emitted when the service state changes |

Both `restart()` and `shutdown()` set `self.exitevent`; systemd restarts the service automatically (`Restart=always`).

**Required callbacks:**

- `dbus_lifecycle_restart() -> bool`
- `dbus_lifecycle_shutdown() -> bool`

---

## ConfigContract

Runtime access to the service configuration. Enabled by `contract_config = true`.

| Member | Type | Description |
|--------|------|-------------|
| `get_config(section, key)` | method | Returns the value as a string |
| `set_config(section, key, value)` | method | Sets a value, returns success |
| `config_changed(section, key, value)` | signal | Emitted when a value changes |

**Required callbacks:**

- `dbus_config_get(section, key) -> str`
- `dbus_config_set(section, key, value) -> bool`

---

## MetricsContract

Operational metrics. Enabled by `contract_metrics = true`.

| Member | Type | Description |
|--------|------|-------------|
| `get_metrics()` | method | Returns metrics as a JSON string |
| `metric_update(name, value)` | signal | Emitted when a metric changes |

The `uptime` value is provided by `HealthContract` and is not duplicated here.

**Required callbacks:**

- `dbus_metrics_get() -> str`

---

## Defining a custom contract

A service defines its own contract by subclassing `ServiceContract`:

```python
from pympacds.contracts import ServiceContract, dbus_method, dbus_signal


class MyContract(ServiceContract):
    iface_name = "MyInterface"
    iface_version = "1.0.0"
    contract_type = "my-domain"

    def __init__(self, ifname, base):
        super().__init__(ifname, base)
        self._require("dbus_my_method")

    @dbus_method()
    def my_method(self, arg: "s") -> "s":
        return self.base.dbus_my_method(arg)

    @dbus_signal()
    def my_event(self, value: "i") -> "i":
        return [value]
```

The service then implements `dbus_my_method(arg)` and exports the contract in `start_dbus()`:

```python
self.iface = MyContract("com.example.MyInterface", self)
self.bus.add_interface("my_object", self.iface)
```

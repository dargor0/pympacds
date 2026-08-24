# pympacds

**Py**thon **M**ulti **P**rocess **A**sync-based **C**ooperative **D**iscoverable **S**ervices Framework

A lightweight, D-Bus-native application framework for building modular, multi-process, asyncio-based daemons on Linux — particularly suited for embedded and IoT systems.

---

## Overview

pympacds provides a common foundation for building systems composed of multiple cooperating daemons that communicate via D-Bus. Each daemon runs as an independent process (managed by systemd as a service), uses Python's `asyncio` for internal concurrency, and automatically discovers peer services on the D-Bus system bus.

The framework is designed for any domain — IoT gateways, industrial control, home automation, automotive, robotics, or any Linux-based distributed system.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    D-Bus System Bus                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Service A │  │ Service B │  │ Service C │  │ Service D │   │
│  │ (sensor)  │  │ (storage) │  │ (comm)    │  │ (ui)      │   │
│  │ ProcessBase│ │ ProcessBase│ │ ProcessBase│ │ ProcessBase│  │
│  │ DBusManager│ │ DBusManager│ │ DBusManager│ │ DBusManager│  │
│  │ task1      │ │ task1      │ │ task1      │ │ task1      │  │
│  │ task2      │ │ task2      │ │            │ │            │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       │              │              │              │         │
│  discover ←────────→ discover ←────→ discover ←──→ discover │
│  (auto peer detection via NameOwnerChanged)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴──────────┐
                    │    systemd (PID 1)  │
                    │  service-a.service  │
                    │  service-b.service  │
                    │  service-c.service  │
                    └────────────────────┘
```

---

## Features

### 1. Process Lifecycle (`ProcessBase`)

The foundation of every service. Provides a complete, well-defined lifecycle:

- **Constructor** — name, version, description for identity
- **Configuration** — INI file loading via `configparser`, CLI arguments via `argparse`
- **Logging** — structured logging with asyncio task-name injection, configurable levels, optional stdout, rotating file logs (4 MiB / 10 backups), auto-created log directories
- **Signal Handling** — graceful shutdown on `SIGHUP`, `SIGTERM`, `SIGINT` via asyncio native signal handlers
- **Abstract start method** — `start_dbus()` must be overridden by subclasses to define D-Bus interfaces
- **Task management** — dictionary of named `asyncio.Task` objects; lazy creation via `update_tasks()` hook
- **Main loop** — `main_loop()` monitors all tasks with `asyncio.wait(FIRST_COMPLETED)`, re-creates finished non-exit tasks, breaks on shutdown
- **Graceful shutdown** — `close_loop()` cancels tasks with timeouts, flushes D-Bus message queue, disconnects
- **Flexible waiting** — `do_waitexit(timeout, events)` combines exit-event watching with optional timeout and additional awaitables

### 2. D-Bus Transport (`DBusManager`)

Abstraction over the D-Bus connection:

- **System bus connection** — `dbus-fast` native asyncio transport
- **Configurable bus name prefix** — all services share a namespace (e.g., `com.example.mysystem`)
- **Automatic prefixing** — bus names without the prefix get it prepended
- **Interface registration** — multiple D-Bus interfaces per object path
- **Name requesting** — `REPLACE_EXISTING` flag, optional `DO_NOT_QUEUE`
- **Proxy creation** — `get_interface()` for remote service interaction
- **Graceful disconnect** — waits for message queue drain (configurable timeout)

### 3. Service Discovery

Automatic peer detection without static configuration:

- **Initial scan** — `ListNames` on `org.freedesktop.DBus` filtered by bus name prefix
- **Dynamic tracking** — subscribes to `NameAcquired`, `NameLost`, `NameOwnerChanged` signals
- **Friend set** — maintains a `set` of known peer bus names, updated in real time
- **Change notification** — `asyncio.Event` signaled on any friend addition or removal
- **Query helpers** — `query_friend_busname(query)`, `is_friend_busname(query)`, `get_friend_bus(busname)`
- **Dynamic subscription** — `connect_friendbus()` handles automatic subscribe/unsubscribe as peers appear and disappear, with a callback for each new or removed friend

### 4. Interface Contracts

Standardized D-Bus interfaces that enforce implementation contracts:

- **Base class** — extends `dbus_fast.service.ServiceInterface`
- **Method validation** — `_test_required_methods()` validates at construction time that the host object provides all required callbacks, raising `AttributeError` if missing
- **Method decorators** — D-Bus methods with type signatures
- **Signal definitions** — D-Bus signals with type signatures
- **Property definitions** — read/write D-Bus properties with type signatures
- **Thin proxy pattern** — interfaces delegate all business logic to `self.base` (the service object)
- **Built-in templates** (pluggable):
  - `HealthContract` — service health and diagnostics (ping, status, heartbeat, uptime)
  - `ConfigContract` — runtime configuration management (get/set config, change notifications)
  - `MetricsContract` — operational metrics exposition (get_metrics, metric updates)
  - `LifecycleContract` — service lifecycle control (restart, shutdown, state changes)
- **Custom interfaces** — applications can define their own interface classes following the same pattern

### 5. Task Management

Patterns for asyncio task lifecycle within a service:

- **Named tasks** — tasks tracked by name in a `dict[str, asyncio.Task]`
- **Lazy creation** — `update_tasks()` hook creates missing tasks on each main loop iteration
- **Periodic tasks** — pattern: `while not exitevent.is_set(): do_work(); await do_waitexit(period)`
- **Clean cancellation** — tasks catch `asyncio.CancelledError` for graceful cleanup
- **Task-aware logging** — custom `asyncioFilter` annotates each log record with the running task name

### 6. Configuration Management

- **INI files** — via `configparser` with `[DEFAULT]` section conventions (primary format)
- **JSON payloads** — JSON as the interchange format for D-Bus method arguments and signal data
- **CLI tool** — `pympacds-admin` for configuration management and service operations

### 7. Utilities

- **Hardware info** — reads device tree (`/sys/firmware/devicetree/base/serial-number`, `model`) for board identification
- **Logging helpers** — stdout logger for CLI tools

### 8. Packaging & Deployment

- **Debian packaging** — `debian/` directory with control, rules, install files
- **Systemd service units** — each daemon is a systemd unit with proper user, restart policy, and D-Bus dependency
- **D-Bus security policy** — `.conf` file for `/etc/dbus-1/system.d/` granting bus name ownership and send permissions
- **pyproject.toml** — modern Python packaging with `[project.scripts]` entry points

---

## Example: Minimal Service

```python
import asyncio
import time
from pympacds.process import ProcessBase
from pympacds.dbus import DBusManager
from pympacds.contracts import ServiceContract, dbus_method, dbus_signal, dbus_property

class MyContract(ServiceContract):
    def __init__(self, ifname, base):
        super().__init__(ifname, base)
        self._require("dbus_my_method")

    @dbus_method(input_signature="s", result_signature="s")
    def my_method(self, arg: str) -> str:
        return self.base.dbus_my_method(arg)

    @dbus_signal(signal_signature="i")
    def heartbeat(self, uptime: int):
        return [uptime]

class MyService(ProcessBase):
    def __init__(self):
        super().__init__(
            name="myservice",
            version="1.0.0",
            description="A minimal pympacds service",
            bus_prefix="com.example.mysystem"
        )

    async def start_dbus(self):
        self.bus = DBusManager(
            logger=self.logger,
            busname="myservice",
            bus_prefix=self.bus_prefix,
        )
        self.iface = MyContract("com.example.MyInterface", self)
        self.bus.add_interface("MyService", self.iface)
        await self.bus.start()
        return True

    def update_tasks(self):
        if "periodic" not in self.tasklist:
            self.tasklist["periodic"] = asyncio.create_task(
                self._periodic(), name="periodic"
            )

    def dbus_my_method(self, arg: str) -> str:
        return f"Got: {arg}"

    async def _periodic(self):
        try:
            while not self.exitevent.is_set():
                await self.do_waitexit(timeout=5)
                self.iface.heartbeat(int(time.time()))
        except asyncio.CancelledError:
            pass

MyService().start()
```

---

## Installation

```bash
pip install pympacds
```

For minimal embedded deployments without the optional Cython extension:

```bash
SKIP_CYTHON=1 pip install --no-binary dbus-fast pympacds
```

---

## Documentation

Detailed documentation lives in the [`doc/`](doc/) directory:

- [Features and Philosophy](doc/features.md)
- [Configuration](doc/config.md)
- [Built-in Contracts](doc/contracts.md)
- [Middleware](doc/middleware.md)

---

## License

MIT

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Service** | A single-purpose daemon that subclasses `ProcessBase` and exports a D-Bus interface |
| **Bus** | D-Bus system bus used as the IPC backbone between services |
| **Discovery** | Automatic peer detection via bus name conventions and `NameOwnerChanged` signals |
| **Interface Contract** | A standardized D-Bus interface template that enforces required callbacks at construction time |
| **Task** | An asyncio coroutine managed inside a service's event loop |
| **Lifecycle** | `setup → init_loop → main_loop → close_loop` pattern inherited from `ProcessBase` |

---

## Related Work

- [dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast) — underlying D-Bus library
- [Venus OS / velib_python](https://github.com/victronenergy/venus) — Victron Energy's D-Bus service framework (energy domain)
- [OpenBMC / phosphor-dbus-interfaces](https://github.com/openbmc/openbmc) — D-Bus microservice framework for server BMCs (C++ based)
- [Wirepas Gateway](https://github.com/wirepas/gateway) — D-Bus based IoT gateway (sink + transport pattern)

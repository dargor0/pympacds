# pympacds

**Py**thon **M**ulti **P**rocess **A**sync-based **C**ooperative **D**iscoverable **S**ervices Framework

A lightweight, D-Bus-native application framework for building modular, multi-process, asyncio-based daemons on Linux — particularly suited for embedded and IoT systems.

## Overview

pympacds provides a common foundation for building systems composed of multiple cooperating daemons that communicate via D-Bus. Each daemon runs as an independent process (managed by systemd as a service), uses Python's `asyncio` for internal concurrency, and automatically discovers peer services on the D-Bus system bus.

The framework is designed for any domain — IoT gateways, industrial control, home automation, automotive, robotics, or any Linux-based distributed system.

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

## Features

pympacds provides:

- **Process lifecycle** — `ProcessBase` with setup/init/main/close, structured logging, signal handling, and named task management.
- **D-Bus transport** — `DBusManager` over `dbus-fast`, configurable namespace prefix, and graceful disconnect.
- **Service discovery** — automatic peer detection and dynamic subscribe/unsubscribe between services.
- **Interface contracts** — validated D-Bus interface definitions, with built-in `Health`, `Lifecycle`, `Config`, and `Metrics` contracts.
- **Middleware** — optional lifecycle extensions (e.g. HTTP config provisioning).

See [Features and Philosophy](doc/features.md) for the full details.

## Example: Minimal Service

```python
import asyncio
import time
from pympacds.process import ProcessBase
from pympacds.dbus import DBusManager
from pympacds.contracts import ServiceContract, dbus_method, dbus_signal


class MyContract(ServiceContract):
    def __init__(self, ifname, base):
        super().__init__(ifname, base)
        self._require("dbus_my_method")

    @dbus_method()
    def my_method(self, arg: "s") -> "s":
        return self.base.dbus_my_method(arg)

    @dbus_signal()
    def heartbeat(self, uptime: "i") -> "i":
        return [uptime]


class MyService(ProcessBase):
    def __init__(self):
        super().__init__(
            name="myservice",
            version="1.0.0",
            description="A minimal pympacds service",
            bus_prefix="com.example.mysystem",
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
            self.tasklist["periodic"] = asyncio.create_task(self._periodic(), name="periodic")

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

## Installation

```bash
pip install pympacds
```

For minimal embedded deployments without the optional Cython extension:

```bash
SKIP_CYTHON=1 pip install --no-binary dbus-fast pympacds
```

## Documentation

Detailed documentation lives in the [`doc/`](doc/) directory:

- [Features and Philosophy](doc/features.md)
- [Configuration](doc/config.md)
- [Built-in Contracts](doc/contracts.md)
- [Middleware](doc/middleware.md)

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Service** | A single-purpose daemon that subclasses `ProcessBase` and exports a D-Bus interface |
| **Bus** | D-Bus system bus used as the IPC backbone between services |
| **Discovery** | Automatic peer detection via bus name conventions and `NameOwnerChanged` signals |
| **Interface Contract** | A standardized D-Bus interface template that enforces required callbacks at construction time |
| **Task** | An asyncio coroutine managed inside a service's event loop |
| **Lifecycle** | `setup → init_loop → main_loop → close_loop` pattern inherited from `ProcessBase` |

## Related Work

- [dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast) — underlying D-Bus library

## License

MIT

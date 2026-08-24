# Features and Philosophy

This page explains what pympacds provides and the reasoning behind its design.

## Philosophy

pympacds is built around a small number of principles:

- **One process per service.** Each concern is a separate daemon managed by systemd. There is no in-process service registry, plugin loader for business logic, or shared state between services.
- **D-Bus is the only IPC.** Services communicate exclusively through the D-Bus system bus. No custom sockets, files, or message queues.
- **asyncio inside, processes outside.** A service is single-threaded and uses asyncio for internal concurrency; cross-service concurrency comes from running as separate processes.
- **Discovery, not configuration.** Services do not hardcode each other's names. They discover peers on the bus and react to them appearing and disappearing.
- **Explicit contracts.** A service declares its D-Bus interface as a contract; the framework validates that the implementation provides the required callbacks at construction time.
- **Fail fast, restart cleanly.** Configuration is validated before the service starts; shutdown is graceful so systemd can restart it safely.

## Core Features

### Process lifecycle (`ProcessBase`)

Every service subclasses `ProcessBase` and inherits a well-defined lifecycle:

```
setup()  →  start()  →  asyncio.run(main_loop())
                         └─ init_loop()  →  main loop  →  close_loop()
```

`ProcessBase` provides:

- CLI argument parsing (`-c/--configfile`)
- INI configuration loading and validation
- Structured logging with asyncio task-name injection
- Rotating file logs and stream logging
- Signal handling (`SIGHUP`, `SIGTERM`, `SIGINT`) for graceful shutdown
- Named asyncio task management with lazy creation via `update_tasks()`
- A `do_waitexit()` primitive for periodic tasks that stay responsive to shutdown

### D-Bus transport (`DBusManager`)

`DBusManager` abstracts the connection to the D-Bus bus:

- Uses `dbus-fast` (falling back to `dbus-next`) via a shared import strategy
- Auto-prefixes bus names with a configurable namespace prefix
- Registers multiple interfaces on multiple object paths, with relative-path expansion against the namespace root
- Requests a bus name and exports interfaces on start; drains the queue and disconnects cleanly on stop
- Provides proxy creation for calling other services

### Service discovery

Services sharing the bus name prefix are "friends". The framework:

- Performs an initial scan via `ListNames`
- Tracks arrivals and departures via `NameAcquired`, `NameLost`, and `NameOwnerChanged` signals
- Maintains a live friend set and signals changes through an `asyncio.Event`
- Reconciles the friend set on each scan, removing names that no longer have an owner
- Provides `connect_friendbus()` for automatic subscribe/unsubscribe as peers come and go

### Interface contracts

A contract is a D-Bus interface definition that delegates business logic to the service object (`self.base`) and validates required callbacks at construction time. See [Built-in Contracts](contracts.md).

### Middleware

Middleware are optional extensions that hook into the service lifecycle to add background tasks or integrate external systems, activated via the INI file. See [Middleware](middleware.md).

### Built-in contracts

pympacds ships four standard contracts, enabled via the `[dbus]` section:

| Contract | Default | Purpose |
|----------|---------|---------|
| `HealthContract` | enabled | Health and diagnostics |
| `LifecycleContract` | off | Remote restart and shutdown |
| `ConfigContract` | off | Runtime configuration access |
| `MetricsContract` | off | Operational metrics |

See [Built-in Contracts](contracts.md) for the members of each.

## Design Decisions

The following decisions are documented in the requirements (`/reqs/`), but are summarised here for reference:

- **`dbus-fast` primary, `dbus-next` fallback** — `dbus-fast` is actively maintained; `dbus-next` provides compatibility.
- **INI for configuration, JSON for D-Bus payloads** — INI is the service configuration format; complex data passed over D-Bus uses JSON strings.
- **Framework config in `[DEFAULT]` and `[dbus]`** — the framework reserves only these two section names; everything else is the service's own.
- **Object paths derive from the bus prefix** — relative paths are expanded against a namespace root, keeping object paths consistent.

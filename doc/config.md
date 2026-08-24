# pympacds Configuration Manual

This document describes the INI configuration file format used by pympacds services, for developers building a service on top of the library.

A service is launched with a configuration file passed on the command line:

```bash
my_service -c /etc/my_service/my_service.ini
```

The framework reads the file in `ProcessBase.setup()` using `configparser`, then validates the built-in keys before the service starts.

---

## Sections

The INI file uses three reserved section names plus any number of service-specific sections:

| Section | Purpose |
|---------|---------|
| `[DEFAULT]` | Framework-level settings (logging) |
| `[dbus]` | D-Bus transport and built-in contract configuration |
| `[middleware]` | Activation of optional middleware extensions |
| `[<anything-else>]` | Service-specific configuration, fully under the developer's control |

`[DEFAULT]` and `[dbus]` are validated by the framework.  All other sections are passed through untouched (optionally validated against a schema — see *Schema Validation*).

---

## `[DEFAULT]` — Logging

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `loglevel` | str | `WARN` | Log level: `DEBUG`, `INFO`, `WARN`, `ERROR`, `CRITICAL` |
| `logstdout` | bool | `false` | Emit log lines to stderr (see *Logging destination*) |
| `logfile` | str | `""` | Path to a rotating log file; empty disables file logging |
| `logfilesuffix` | str | `""` | Name of a CLI argument whose value is inserted into the log file name |
| `logformat` | str | *(see below)* | Log message format string |
| `logdate` | str | `%Y-%m-%dT%H:%M:%S` | Date/time format used by `logformat` |
| `logmaxsize` | int | `4194304` | Maximum log file size in bytes (4 MiB) |
| `logmaxcount` | int | `10` | Number of rotated backup log files to retain |

Default `logformat`:

```
%(asctime)s - %(levelname)-8s - %(taskid)-10s - %(name)s: %(message)s
```

The `%(taskid)s` field is injected by the framework's asyncio filter and shows the name of the asyncio task that emitted each line, so concurrent task activity can be traced in the log.

### Logging destination

The `logstdout` and `logfile` keys control where log lines go:

- **`logstdout = true`** (recommended for systemd) — a `StreamHandler` writes log lines to **stderr**.  systemd captures stderr into the journal
  by default (`StandardError=journal`), so no log file is needed.
- **`logfile = /path/to/file.log`** — a rotating file handler writes log lines to the given file.  Useful for interactive development or when
  journald is unavailable.

Both may be enabled at once; they share the same formatter, level, and asyncio task filter.

---

## `[dbus]` — D-Bus Transport and Contracts

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `bus_type` | str | `system` | Bus to connect to: `system` or `session` |
| `bus_prefix` | str | `org.pympacds` | Namespace prefix shared by all services of the system |
| `drain_timeout_ms` | int | `2000` | Milliseconds to wait for the outgoing queue to drain on shutdown |
| `name_request_flags` | str | `REPLACE_EXISTING` | Bus name request flags (see below) |
| `discovery_enabled` | bool | `true` | Track peer services sharing the prefix (friend bus) |

### Built-in contracts

The following keys enable or disable built-in D-Bus interface contracts. Enabled contracts are exported automatically when the service starts:

| Key | Default | Contract | Description |
|-----|---------|----------|-------------|
| `contract_health` | `true` | `HealthContract` | Health/diagnostics (`ping`, `status`, `heartbeat`, `uptime`) |
| `contract_metrics` | `false` | `MetricsContract` | Operational metrics exposition |
| `contract_lifecycle` | `false` | `LifecycleContract` | Remote restart and shutdown |
| `contract_config` | `false` | `ConfigContract` | Runtime configuration management |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `heartbeat_interval_s` | int | `30` | Seconds between automatic `heartbeat` signal emissions (5–3600) |

The contract keys are configurable **only** via the INI file (or environment override); they have no constructor parameter.

### Name request flags

`name_request_flags` accepts a comma- or space-separated list of any of:

- `ALLOW_REPLACEMENT` — allow another process to take over the name
- `REPLACE_EXISTING` — take over the name if already owned
- `DO_NOT_QUEUE` — fail if the name cannot be acquired immediately

---

## `[middleware]` — Middleware Activation

Optional middleware extensions hook into the service lifecycle to add background tasks or integrate external systems.

Each entry maps a middleware entry-point name to the INI section holding its configuration:

```ini
[middleware]
httpconfprov = http_config
```

- The **key** is the middleware name (a Python entry point).
- The **value** is the name of another section in this file that holds the middleware's parameters.
- Use the special value `none` when a middleware needs no configuration section.

The same middleware may be listed more than once under different keys to instantiate it multiple times with different configuration sections.

---

## Environment Variable Overrides

Every configuration value can be overridden via an environment variable of the form `PYMPACDS_<SECTION>_<KEY>` (uppercase, underscores).  Environment
variables take precedence over INI file values.

Precedence, highest to lowest:

```
constructor argument  >  environment variable  >  INI file value  >  framework default
```

Example:

```bash
PYMPACDS_DEFAULT_LOGLEVEL=DEBUG PYMPACDS_DBUS_BUS_TYPE=session \
    my_service -c dev.ini
```

---

## Schema Validation

In addition to the built-in validation of `[DEFAULT]` and `[dbus]`, a service may validate its own sections against a JSON schema file.

```json
{
    "myservice": {
        "required": ["device_id"],
        "keys": {
            "device_id": {"type": "str", "pattern": "^[A-Z]{2}[0-9]{4}$"},
            "period_s":  {"type": "int", "min": 1, "max": 3600},
            "verbose":   {"type": "bool", "default": false}
        }
    }
}
```

Schema properties per key:

| Property | Meaning |
|----------|---------|
| `required` | List of keys that must be present |
| `type` | `str`, `int`, `float`, or `bool` |
| `min` / `max` | Numeric range (inclusive) |
| `pattern` | Regular expression for string values |
| `default` | Fallback value when the key is absent |

The schema file path is specified via:

1. The `--schema` argument to `pympacds-admin validate`
2. A `schema` key in `[tool.pympacds]` in `pyproject.toml`
3. A constructor argument to `ConfigManager`

Schema validation is controlled by `[dbus] validate_user_schema` (default `true`).  When `false`, only built-in validation runs.

---

## Example

```ini
[DEFAULT]
loglevel = INFO
logstdout = true

[dbus]
bus_type = system
bus_prefix = org.example.mysystem
discovery_enabled = true
contract_health = true

[middleware]
httpconfprov = provisioning

[provisioning]
url = https://provision.example.com/device/%s
timeout_s = 10

[myservice]
device_id = AB1234
period_s = 5
```

The `%s` placeholder in middleware configuration is replaced with the service name at runtime.

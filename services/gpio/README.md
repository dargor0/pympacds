# pympacds-gpio

GPIO server service for [pympacds](https://github.com/dargor0/pympacds). Monitors
GPIO line state and edge events and exposes reads, writes, and change
notifications over D-Bus.

## Features

- Reads (`get_value`, `get_lines`) and writes (`set_value`) of GPIO lines over
  D-Bus, with per-output-line default (reset) values
  (`get_default_value` / `set_default_value`).
- Edge-event watching (`line_changed` signal) driven by the kernel
  `gpiod` edge-event API, polled asynchronously with a short timeout so the
  event loop is never stalled.
- Kernel-side debounce via `debounce_us` (passed to libgpiod), and
  `active_low` inversion for watched lines. No software debounce.
- A configurable line-name mapping (`[gpio.linemap]`) so lines can be
  referenced by human-readable name or numeric offset on the D-Bus API.
- Runtime configuration: `[gpio]` keys are writable via the framework
  `ConfigContract`.
- Exports the framework `HealthContract` (always) and `ConfigContract`
  (opt-in via `[dbus] contract_config = true`) alongside the GPIO contract.

### Why gpiod

The service uses the [gpiod](https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git/)
Python bindings (libgpiod v2 API) because it is the reference userspace
interface to the kernel GPIO character-device subsystem: line requests, edge
events, and debounce are all handled in the kernel. It is isolated in this
service's own package, so the core `pympacds` framework keeps its
zero-dependency property.

## Installation

```bash
pip install pympacds-gpio
```

## Configuration

The service reads its parameters from the `[gpio]` INI section and the optional
`[gpio.linemap]` section (see `config/gpio.ini.example`):

| Key | Default | Description |
|-----|---------|-------------|
| `chip` | `""` | GPIO chip path (empty = auto-detect) |
| `lines` | `""` | Comma-separated line names/offsets to expose and read |
| `watch_lines` | `""` | Lines watched for edge events |
| `edge` | `both` | Edge to watch: `rising`, `falling`, `both` |
| `debounce_us` | `0` | Kernel debounce period (µs) for watched lines |
| `active_low` | `false` | Invert line logic for watched lines |
| `output_lines` | `""` | Lines driven as outputs |
| `default_values` | `""` | `line=value` pairs for output defaults (e.g. `relay1=0,5=1`) |

The `[gpio.linemap]` section maps human-readable names to offsets (`btn = 0`).
Names must not begin with a digit.

Run with:

```bash
pympacds-gpio -c /etc/pympacds/gpio.ini
```

## Device access (permissions)

The service does **not** require root. Access to `/dev/gpiochip*` is granted
via a udev rule (shipped by the Debian package) that assigns the device to the
`gpio` group with mode `0660`; add the service user to the `gpio` group:

```bash
sudo usermod -aG gpio <service-user>
```

## D-Bus API

Interface `org.pympacds.GPIO` at object path `/org/pympacds/gpio`:

| Member | Type | Description |
|--------|------|-------------|
| `get_value(line)` | `get_value(s) -> b` | Read a line's value |
| `set_value(line, value)` | `set_value(sb) -> b` | Set an output line |
| `get_default_value(line)` | `get_default_value(s) -> b` | Default (reset) value of an output |
| `set_default_value(line, value)` | `set_default_value(sb) -> b` | Set the default value |
| `get_lines()` | `get_lines() -> s` | JSON values of all exposed lines |
| `status()` | `status() -> s` | JSON chip/line configuration + values |
| `line_changed(name, offset, value)` | signal `(sub)` | Emitted on a watched edge event |
| `watched_lines` | property `(as)` | List of watched lines |

A `line` argument is a decimal offset or a `[gpio.linemap]` name, resolved
number-first-then-name. An unresolvable line produces a D-Bus error reply.

The framework `HealthContract` (at `.../health`) and `ConfigContract`
(`[dbus] contract_config = true`) are also exported.

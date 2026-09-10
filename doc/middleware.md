# Middleware

Middleware are optional framework extensions that hook into the service lifecycle. Unlike contracts (which export D-Bus interfaces), middleware adds background tasks, integrates external systems, or modifies startup behaviour. Middleware is activated exclusively through the INI file.

## Activation

The `[middleware]` section maps a middleware entry-point name to the INI section holding its configuration:

```ini
[middleware]
httpconfprov = http_config
```

- The **key** is the middleware name (a Python entry point registered under the `pympacds.middleware` group).
- The **value** is the name of another section in this file that holds the middleware's parameters.
- Use the special value `none` when a middleware needs no configuration section.

The same middleware may be listed more than once under different keys to instantiate it multiple times with different configuration sections.

## Lifecycle hooks

A middleware is a class with the following hooks:

```python
class MiddlewareBase:
    def __init__(self, service, section):
        """Called before start_dbus(). Read configuration here (synchronous)."""

    async def setup(self):
        """Called after start_dbus() succeeds. Add tasks, create proxies."""

    async def teardown(self):
        """Called during close_loop(), before bus.stop()."""

    def on_error(self, error):
        """Called when setup() or teardown() raises. Return True to continue."""
```

The framework calls these hooks in order during `init_loop()` and `close_loop()` (see `ProcessBase` in the API).

**Non-fatal by design.** Middleware should not abort the service. A
misconfigured or failing middleware logs a warning and is skipped or disabled
(`on_error()` returns `true` to continue, rather than `false` to abort). The
built-in middleware never raise from `setup()`/`teardown()` — see the
`httpconfprov` section below and [Signal-Action Middleware](signal_action.md).

## Discovery

Middleware classes are discovered via Python entry points. A middleware package declares itself in its `pyproject.toml`:

```toml
[project.entry-points."pympacds.middleware"]
httpconfprov = "pympacds.middleware:HttpConfigMiddleware"
```

The framework loads the class lazily, only for middleware enabled in the INI file.

## Accessing a middleware instance

A service can obtain an activated middleware instance to call its public APIs at
runtime (for example, `signal_action.register_rule()`). `ProcessBase` provides:

```python
def get_middleware(self, ident: type | str) -> MiddlewareBase | None
```

- `ident` is a `MiddlewareBase` subclass (matched by exact class) or the
  entry-point name string (e.g. `"signal_action"`).
- Returns the singleton instance, or `None` if the middleware is not activated.

```python
mw = self.get_middleware(SignalActionMiddleware)
if mw is not None:
    await mw.register_rule("gpio_to_http", "@gpio:line_changed", "@http:send")
```

## Built-in middleware: `httpconfprov`

The framework ships two built-in middleware — `httpconfprov` (this section) and
`signal_action` ([Signal-Action Middleware](signal_action.md)). `httpconfprov`
downloads the service configuration from an HTTP server at startup.

### Purpose

Enables zero-touch provisioning: a device boots with a minimal bootstrap config, the middleware fetches the full configuration, writes it to the INI file, and triggers a restart so the new configuration takes effect.

### Configuration

```ini
[middleware]
httpconfprov = provisioning

[provisioning]
url = https://provision.example.com/device/%s
timeout_s = 10
tls_verify = true
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `url` | str | — (required) | HTTP endpoint returning JSON config. `%s` is replaced with the service name. |
| `timeout_s` | int | `10` | HTTP request timeout in seconds |
| `tls_verify` | bool | `true` | Verify TLS certificates |

### Behaviour

1. On `setup()`, performs an HTTP GET to `url`.
2. On failure (timeout, DNS, connection refused), logs a warning and continues with the existing configuration.
3. If the response matches the current local configuration, no action is taken.
4. If the response differs, it writes the new configuration to the INI file, logs the change, and triggers a restart via `self.exitevent`.

Only the Python standard library is used (`urllib.request`).

### Use cases

| Scenario | Example URL |
|----------|-------------|
| Zero-touch provisioning | `https://provision.example.com/device/%s` |
| Fleet-wide config push | `https://config.example.com/model/ABC/%s` |
| Identity-based config | `https://config.example.com/?serial=%s` |
| Primary + fallback | Two `httpconfprov` entries pointing at different servers |

## Built-in middleware: `signal_action`

`signal_action` lets a service react to D-Bus signals from other services and,
on each received signal, invoke a configured target method — driven entirely by
configuration:

```ini
[middleware]
signal_action = signal_rules

[signal_rules]
gpio_to_http = {"trigger": "@gpio:line_changed", "action": "@http:send"}
```

It resolves `@capability` tags through friend discovery and introspection, maps
signal arguments to target-method arguments, and re-arms persistent rules as
peers come and go. See [Signal-Action Middleware](signal_action.md) for the full
rule syntax, argument mapping, and D-Bus export.

## Writing a custom middleware

Use the scaffolding tool to generate a middleware package:

```bash
pympacds-admin new-middleware my_middleware
```

This creates a package skeleton with the correct entry-point declaration and a `MiddlewareBase` subclass. Fill in the hooks, publish to PyPI, and users enable it in their `[middleware]` section — no framework changes required.

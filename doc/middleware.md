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

## Discovery

Middleware classes are discovered via Python entry points. A middleware package declares itself in its `pyproject.toml`:

```toml
[project.entry-points."pympacds.middleware"]
httpconfprov = "pympacds.middleware:HttpConfigMiddleware"
```

The framework loads the class lazily, only for middleware enabled in the INI file.

## Built-in middleware: `httpconfprov`

The framework ships one built-in middleware, `httpconfprov`, which downloads the service configuration from an HTTP server at startup.

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

## Writing a custom middleware

Use the scaffolding tool to generate a middleware package:

```bash
pympacds-admin new-middleware my_middleware
```

This creates a package skeleton with the correct entry-point declaration and a `MiddlewareBase` subclass. Fill in the hooks, publish to PyPI, and users enable it in their `[middleware]` section — no framework changes required.

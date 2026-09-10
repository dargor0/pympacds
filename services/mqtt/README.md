# pympacds-mqtt

MQTT client service for [pympacds](https://github.com/example/pympacds). Maintains
a single MQTT connection to a broker and exposes publish/subscribe over D-Bus,
with automatic reconnection.

## Features

- A single, always-on MQTT connection with **automatic reconnection** using
  exponential backoff (`reconnect_min_s` … `reconnect_max_s`), re-subscribing
  all active topics after each reconnect.
- Publish/subscribe over D-Bus: `publish`, `send`, `subscribe`, `unsubscribe`,
  `reconnect`, and `status`, plus a `message_received` signal and a read-only
  `connected` property.
- MQTT **3.1.1** and **5.0**; TLS (CA cert, client cert/key, and a
  development-only `tls_insecure` mode); username/password auth; and
  Last Will & Testament (LWT).
- A bounded inbound message queue (drop-oldest on overflow; `queue_size = 0`
  means unbounded).
- Runtime configuration: `[mqtt]` keys are writable via the framework
  `ConfigContract`; connection parameters are staged and applied with an
  explicit `reconnect()`.
- Exports the framework `HealthContract` (always) and `ConfigContract`
  (opt-in via `[dbus] contract_config = true`) alongside the MQTT contract.

### Why paho-mqtt

The service uses [paho-mqtt](https://www.eclipse.org/paho/) as its MQTT engine
because it is the reference Eclipse Paho implementation: it supports MQTT 3.1,
3.1.1, and 5.0, and is pure Python with **no transitive dependencies**. No
third-party asyncio wrapper (e.g. `aiomqtt`) is used — the asyncio adapter is
owned by this project (see below), so the dependency surface stays minimal and
the framework keeps full control over its event loop.

### Asyncio shim

paho-mqtt is a blocking, callback-driven library, so `pympacds-mqtt` wraps it in
an internal **shim** (`pympacds_mqtt.shim`) — the only module that imports
`paho.mqtt`. It adapts paho's "external event loop" mode to the framework's
single-asyncio-loop model:

- **Socket integration** — `on_socket_open` / `on_socket_close` /
  `on_socket_register_write` / `on_socket_unregister_write` map to
  `loop.add_reader` / `loop.add_writer`, so `loop_read` / `loop_write` run on
  the event loop and never block it.
- **Non-blocking connect** — the hostname is resolved with
  `await loop.getaddrinfo(...)`, then `connect_async(...)` drives the TCP/TLS
  handshake through the socket callbacks until `on_connect` fires (bounded by
  `connect_timeout_s`).
- **Loop cadence** — `loop_misc()` (pings, QoS retries) runs from a periodic
  task at `loop_interval_s`.
- **Message bridge** — `on_message` enqueues into a bounded `asyncio.Queue`
  (drop-oldest, never blocking); the service drains it with
  `async for topic, payload in shim.messages()`.
- **Reconnection** — `on_disconnect` starts an exponential-backoff reconnect
  loop that re-subscribes all topics on success; a graceful `disconnect()` /
  `stop()` never triggers it.

## Installation

```bash
pip install pympacds-mqtt
```

## Configuration

The service reads its parameters from the `[mqtt]` INI section (see
`config/mqtt.ini.example`). Key options:

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `localhost` | Broker hostname/IP |
| `port` | `1883` | Broker port |
| `keepalive` | `60` | Keep-alive interval (seconds) |
| `username` / `password` | `""` | Optional credentials |
| `use_tls` | `false` | Enable TLS |
| `subscribe_topics` | `""` | Comma-separated topics subscribed at startup |
| `default_topic` | `""` | Topic used by `send()` (empty = `send()` fails) |
| `queue_size` | `1000` | Max queued inbound messages (0 = unbounded) |

Run with:

```bash
pympacds-mqtt -c /etc/pympacds/mqtt.ini
```

## D-Bus API

Interface `org.pympacds.MQTT` at object path `/org/pympacds/mqtt`:

| Member | Type | Description |
|--------|------|-------------|
| `publish(topic, payload)` | `publish(ss) -> b` | Publish a message |
| `send(payload)` | `send(s) -> b` | Publish to `default_topic` |
| `subscribe(topic)` | `subscribe(s) -> b` | Subscribe at runtime |
| `unsubscribe(topic)` | `unsubscribe(s) -> b` | Unsubscribe |
| `reconnect()` | `reconnect() -> b` | Rebuild client and reconnect |
| `status()` | `status() -> s` | JSON connection snapshot |
| `message_received(topic, payload)` | signal `(ss)` | Emitted on each received message |
| `connected` | property `(b)` | Connection state |

The framework `HealthContract` (at `.../health`) and `ConfigContract`
(`[dbus] contract_config = true`) are also exported.

# Capability Tags — Suggested Vocabulary

This is a **suggested, non-enforced** vocabulary of capability tags for
pympacds services. It is a shared starting point to reduce fragmentation in how
services describe themselves (`provides`) and what they depend on
(`requires`). It is **not** a registry, **not** a validation rule, and **not**
introspection: the framework never enforces membership in this list, and it is
never consulted at runtime.

## Format rules

- Tags are lowercase, with words separated by hyphens (e.g. `publish-subscribe`),
  and no spaces.
- A third-party tag that needs disambiguation may use a reverse-DNS prefix
  (e.g. `com.acme.foo`).
- The same tag may appear as both a `provides` and a `requires` tag: a tag
  describes a *capability offered* (`provides`) or a *dependency needed*
  (`requires`).

## Grouped tag list

Each tag below includes its suggested D-Bus contract — the methods, signals, and
properties (with D-Bus type signatures) that a service implementing the tag is
expected to expose. This is a **convention, not an enforcement**: the framework
does not validate a service's interfaces against this list, and a service may
deviate. A tag-based consumer that needs the real contract resolves it by
introspection (`BusIntrospect`, REQ-DBUS-013), never from this file.

### Messaging

| Tag | Description | Suggested contract |
|-----|-------------|--------------------|
| `mqtt` | Maintains an MQTT connection and exposes publish/subscribe | `publish(ss) -> b`, `send(s) -> b`, `subscribe(s) -> b`, signal `message_received(ss)`, property `connected(b)` |
| `publish-subscribe` | Pub/sub messaging capability | (generic; see `mqtt`) |

### Networking

| Tag | Description | Suggested contract |
|-----|-------------|--------------------|
| `http` | Sends data over HTTP | `send(s) -> s`, `download(ss) -> s`, `request(sssss) -> s`, signal `request_completed(ss)` |
| `network` | Requires network access | (a `requires` tag; no contract) |

### Hardware

| Tag | Description | Suggested contract |
|-----|-------------|--------------------|
| `gpio` | Monitors/drives GPIO lines | `get_value(s) -> b`, `set_value(sb) -> b`, signal `line_changed(sub)`, property `watched_lines(as)` |

### Telemetry

| Tag | Description | Suggested contract |
|-----|-------------|--------------------|
| `metrics` | Exposes operational metrics | `get_metrics() -> s` |
| `health` | Exposes health/diagnostics | `ping() -> b`, `status() -> s`, property `uptime(u)` |

## Built-in / core tags

Tags used by the framework's own contracts:

- `health`
- `config`
- `metrics`
- `lifecycle`

## How to add a tag

Adding a tag is additive and non-breaking. A tag may be added to this file
without any core change or release. Prefer reusing an existing tag over
introducing a near-duplicate.

## Usage examples

The current standard services declare their tags as:

- MQTT service: `provides: ["mqtt", "publish-subscribe"]`, `requires: ["network"]`
- HTTP service: `provides: ["http"], requires: ["network"]`
- GPIO service: `provides: ["gpio"]`

## What tags are not

Tags are **not** introspection (introspection lists the actual interfaces,
methods, signals, and properties). Tags are **not** enforced. Tags are **not** a
security boundary. Tags are **not** a discovery guarantee — a service may omit
or misreport tags, and consumers must treat them as hints.

## Discovery usage

A consumer filters friends by capability at runtime via the `provides`/
`requires` properties on the `HealthContract` (type `as`):

```python
proxy = await bus.get_friend_bus(friend_busname, ifname="org.pympacds.Health")
tags = await proxy.get_provides()  # or get_requires()
```

## Changelog

- (none yet)

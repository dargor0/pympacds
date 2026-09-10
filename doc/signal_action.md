# Signal-Action Middleware

The built-in `signal_action` middleware lets a service react to D-Bus signals
from other services and, on each received signal, invoke a configured target
method. It generalizes the "react to a signal" pattern so that no service needs
bespoke trigger logic — the behaviour is driven by the INI file or by rules
registered in code.

It is a `MiddlewareBase` (see [Middleware](middleware.md)): enabled via the
`[middleware]` section, subscribing in `setup()` and unsubscribing in
`teardown()`.

## Activation

The `[middleware]` section maps the middleware name to its configuration
section:

```ini
[middleware]
signal_action = signal_rules
```

`[signal_rules]` defines **rules**, one per key. Each rule value is a JSON
object:

```ini
[signal_rules]
gpio_to_http = {"trigger": "@gpio:line_changed", "action": "@http:send", "notify": true}
http_to_mqtt = {"trigger": "org.pympacds.http:org.pympacds.HTTP:request_completed", "action": "@mqtt:publish"}
gpio_to_mqtt = {"trigger": "@gpio:line_changed", "action": "@mqtt:publish", "argmap": "[\"sensors/{0}\", \"{2}\"]"}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `trigger` | str | — (required) | Signal spec (see below) |
| `action` | str | — (required) | Action spec (see below); tag (`@tag:method`) or explicit form |
| `persistent` | bool | `true` | Whether the rule re-arms as the signal source appears/disappears |
| `notify` | bool | `false` | Emit `signal_action_completed` for each action outcome |
| `argmap` | str\|null | `null` | Argument mapping: `null` = JSON passthrough, else a template |
| `queue_size` | int | `1000` | Bound of the pending-signal dispatch queue |

## Programmatic rule registration

Rules can also be registered and modified from code at runtime, in addition to
the `[signal_rules]` INI section. A service obtains the middleware instance via
`ProcessBase.get_middleware()` (see [Middleware](middleware.md)) and calls:

```python
mw = self.get_middleware(SignalActionMiddleware)
if mw is not None:
    ok = await mw.register_rule("gpio_to_http", "@gpio:line_changed", "@http:send")
    if not ok:
        self.logger.error("failed to register rule")
```

```python
async def register_rule(
    self,
    name: str,
    trigger: str,
    action: str,
    *,
    persistent: bool = True,
    notify: bool = False,
    argmap: str | None = None,
    queue_size: int = 1000,
) -> bool

async def remove_rule(self, name: str) -> bool
```

Field semantics match the INI rules. `register_rule()` performs **strict
registration-time validation**: both `trigger` and `action` must parse and be
resolvable at that moment, otherwise it returns `false` — even with
`persistent: true`. Once registered, `persistent` governs availability as usual
(the rule deactivates/reactivates as its source appears and disappears).
`remove_rule()` unsubscribes the trigger and cancels the rule's dispatcher.

> **Note:** `register_rule()` and `remove_rule()` are async and require a live
> bus connection to resolve the trigger/action. The intended call sites are
> `start_dbus()` (after `await self.bus.start()`, where the rule is staged and
> armed during `setup()`) or any runtime async task (where it arms immediately).

## Signal spec (trigger)

A signal spec selects the signal to watch, in one of two forms.

**Tag form (preferred):** `@<capability>:<member>`

- `<capability>` is a capability tag from the suggested vocabulary
  ([tags](tags.md)). The source service is found by scanning the friend set for
  a peer whose `provides` property includes the tag.
- `<member>` is the signal name and is always explicit.

The interface and bus name are **not** assumed from the tag: the middleware
introspects each matched friend (`DBusManager.introspect_tree()`) and uses
`BusIntrospect.find_signal(member)` to locate the interface(s) that declare the
signal, then subscribes to each.

**Explicit form:** `<busname>:<interface>:<member>`

- `<busname>` — a well-known bus name or a short friend name resolved via
  discovery.
- `<interface>` — the emitting interface.
- `<member>` — the signal member name.

If a tag trigger resolves to more than one service — or more than one interface
within a service declares the member — the middleware subscribes to **every**
matching signal.

## Action spec (target)

An action spec selects the target method.

**Tag form (preferred):** `@<capability>:<method>`

- `<method>` is the target method name and is **required** — there is no default
  method. A tag action without `:method` is invalid: the middleware logs a
  warning and disables that rule.
- As with triggers, the object path and interface are discovered by
  introspection (`find_method()`), so third-party alternatives are supported.

**Explicit form (escape hatch):**
`<busname>:<object_path>:<interface>:<method>`

If a tag action resolves to multiple targets, the middleware invokes **every**
matching method concurrently (one asyncio task per target).

## Argument mapping

Each rule derives the target method's arguments from the signal's arguments via
the `argmap` field. Both modes are implemented.

- **`argmap: null` (default) — JSON passthrough.** The signal's arguments are
  JSON-encoded and passed as the target method's **first** argument. For HTTP
  this maps directly to `send(payload)`.
- **`argmap: "<template>"` — argument template.** A JSON array of argument
  expressions, one per target-method argument. Each expression is a string with
  placeholders:
  - `{name}` — the signal argument with that name;
  - `{N}` — the signal argument at zero-based position `N`;
  - literal text is emitted as-is.

  Each resolved expression is then **coerced to the target argument's D-Bus
  type** from the introspected signature (`s` as-is, with non-string values
  stringified as `true`/`false` or decimal; `b` parses `true`/`false`/`1`/`0`;
  `i`/`u`/`x`/`t`/`d` parse numerically). A placeholder that does not resolve, or
  a coercion failure, logs a warning and drops the action.

Example — map GPIO `line_changed(name, offset, value)` to MQTT
`publish(topic, payload)`:

```ini
gpio_to_mqtt = {"trigger": "@gpio:line_changed", "action": "@mqtt:publish",
                "argmap": "[\"sensors/{0}\", \"{2}\"]"}
```

## Persistent rules

Rules are **persistent by default**.

- **`persistent: true`** — the middleware watches the friend set and subscribes
  to the trigger signal when a matching service appears, and unsubscribes when it
  disappears. The rule is enabled only while its source is available and re-arms
  automatically after peer restarts or startup reordering.
- **`persistent: false`** — subscribe once at `setup()`. If the source is not
  present, log a warning and drop the rule (never retried).

The `persistent` flag governs the trigger (signal source); the action target is
resolved lazily at dispatch time and does not need the flag.

## Execution semantics

- **Fire-and-forget.** A signal has no reply path, so each action runs as an
  asyncio task spawned at signal receipt; the middleware never blocks the event
  loop or the signal callback.
- **Backpressure.** Received signals awaiting dispatch are held in a bounded
  queue sized by the rule's `queue_size`; on overflow the middleware logs a
  warning and drops the **oldest** entry.
- **Ordering.** Actions for a given rule are dispatched in signal order;
  concurrent execution across rules is permitted.
- **Result.** Action outcomes are always logged (INFO on success, WARNING on
  failure). When `notify` is set, a `signal_action_completed` signal is emitted
  for each outcome.

## Error handling & retry

Error handling is **non-fatal**: a misconfigured rule never aborts the service.

- A rule value that is **not valid JSON** is logged at warning and **skipped**.
- A valid-JSON rule with an **invalid spec** (missing `trigger`/`action`, or a
  `@tag` action without `:method`) is logged at warning and **disabled**.
- **No rules at all** (missing/empty section) is a valid state: the middleware
  starts with an empty rule set and waits for `register_rule()` calls.
- **Programmatic registration** — `register_rule()` is strict: it returns
  `false` if the `trigger` or `action` does not parse or resolve at that moment,
  regardless of `persistent` (see
  [Programmatic rule registration](#programmatic-rule-registration)).
- **Retry** — a **persistent** rule retries a failed action when the friend set
  changes (`wait_friend_changes()`); a **non-persistent** rule has no retry.
  Exceptions raised by an action are caught and logged; they never propagate to
  the signal callback.

## D-Bus export

The middleware registers the `org.pympacds.SignalAction` contract so operators
can inspect rules and their runtime status.

- **`get_rules() -> s`** — a JSON array of rule objects:

  ```json
  [
    {"name": "gpio_to_http", "trigger": "@gpio:line_changed",
     "action": "@http:send", "persistent": true, "notify": true,
     "argmap": null, "active": true}
  ]
  ```

  | Field | Description |
  |-------|-------------|
  | `name` | The rule key from `[signal_rules]` |
  | `trigger` | The signal spec |
  | `action` | The action spec |
  | `persistent` | Whether the rule re-arms dynamically |
  | `notify` | Whether the rule emits `signal_action_completed` |
  | `argmap` | The argument mapping (`null` or template) |
  | `active` | Whether the trigger subscription is currently established |

- **`rule_status_changed(name, active)` signal (`sb`)** — emitted whenever a
  rule's `active` status changes.
- **`signal_action_completed(rule, result)` signal (`ss`)** — emitted for each
  action outcome of a rule whose `notify` flag is set.

A rule is `active` while its trigger subscription is established, and `inactive`
when the source is unavailable or the rule was dropped.

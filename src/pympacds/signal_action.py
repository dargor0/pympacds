"""signal_action middleware — react to D-Bus signals with configured actions.

Implements REQ-MIDW-004 through REQ-MIDW-014.  A ``signal_action`` middleware
subscribes to D-Bus signals from other services and, on each received signal,
invokes a configured target method, driven entirely by the INI file::

    [middleware]
    signal_action = signal_rules

    [signal_rules]
    gpio_to_http = {"trigger": "@gpio:line_changed", "action": "@http:send",
                    "notify": true}
"""

import asyncio
import json
import re
from dataclasses import dataclass, field

from .contracts import ServiceContract, dbus_method, dbus_signal
from .middleware import MiddlewareBase

__all__ = ["SignalActionMiddleware", "SignalActionContract", "Rule"]


# ----------------------------------------------------------------------
# parsed specs
# ----------------------------------------------------------------------


@dataclass
class TriggerSpec:
    """A parsed signal spec (REQ-MIDW-006)."""

    kind: str  # "tag" | "explicit"
    raw: str
    capability: str | None = None  # tag form
    member: str = ""  # signal member name
    busname: str | None = None  # explicit form
    interface: str | None = None  # explicit form


@dataclass
class ActionSpec:
    """A parsed action spec (REQ-MIDW-007)."""

    kind: str  # "tag" | "explicit"
    raw: str
    capability: str | None = None  # tag form
    method: str = ""  # target method name
    busname: str | None = None  # explicit form
    object_path: str | None = None  # explicit form
    interface: str | None = None  # explicit form


@dataclass
class SignalEvent:
    """A received signal: positional args plus a name->value map."""

    args: list
    by_name: dict


@dataclass
class Rule:
    """One resolved signal-action rule."""

    name: str
    trigger: TriggerSpec
    action: ActionSpec
    persistent: bool = True
    notify: bool = False
    argmap: list | None = None
    queue_size: int = 1000
    active: bool = False
    queue: asyncio.Queue = field(default=None, repr=False)
    subscriptions: list = field(default_factory=list, repr=False)
    pending: list = field(default_factory=list, repr=False)


def _parse_trigger(spec: str) -> TriggerSpec | None:
    """Parse a trigger (signal) spec; ``None`` on invalid (REQ-MIDW-006)."""
    spec = spec.strip()
    if not spec:
        return None
    if spec.startswith("@"):
        body = spec[1:]
        if ":" not in body:
            return None
        cap, member = body.split(":", 1)
        cap = cap.strip()
        member = member.strip()
        if not cap or not member:
            return None
        return TriggerSpec(kind="tag", raw=spec, capability=cap, member=member)
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) != 3 or any(not p for p in parts):
        return None
    return TriggerSpec(
        kind="explicit",
        raw=spec,
        busname=parts[0],
        interface=parts[1],
        member=parts[2],
    )


def _parse_action(spec: str) -> ActionSpec | None:
    """Parse an action spec; ``None`` on invalid (REQ-MIDW-007).

    A ``@tag`` action missing ``:method`` is invalid (returns ``None``).
    """
    spec = spec.strip()
    if not spec:
        return None
    if spec.startswith("@"):
        body = spec[1:]
        if ":" not in body:
            return None  # @tag without :method
        cap, method = body.split(":", 1)
        cap = cap.strip()
        method = method.strip()
        if not cap or not method:
            return None
        return ActionSpec(kind="tag", raw=spec, capability=cap, method=method)
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) != 4 or any(not p for p in parts):
        return None
    return ActionSpec(
        kind="explicit",
        raw=spec,
        busname=parts[0],
        object_path=parts[1],
        interface=parts[2],
        method=parts[3],
    )


# ----------------------------------------------------------------------
# D-Bus contract (REQ-MIDW-014)
# ----------------------------------------------------------------------


class SignalActionContract(ServiceContract):
    """Exposes rules and runtime status over D-Bus."""

    iface_name = "SignalAction"

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require("signal_action_get_rules")

    @dbus_method()
    def get_rules(self) -> "s":
        return self.base.signal_action_get_rules()

    @dbus_signal()
    def rule_status_changed(self, name: "s", active: "b") -> "sb":
        return [name, active]

    @dbus_signal()
    def signal_action_completed(self, rule: "s", result: "s") -> "ss":
        return [rule, result]


# ----------------------------------------------------------------------
# middleware
# ----------------------------------------------------------------------


class SignalActionMiddleware(MiddlewareBase):
    """Subscribes to signals and invokes configured actions."""

    def __init__(self, service, section):
        super().__init__(service, section)
        self._rules: dict[str, Rule] = {}
        self._fatal: str | None = None
        self._contract = None
        self._dispatcher_tasks: dict[str, asyncio.Task] = {}
        self._rearm_task: asyncio.Task | None = None
        self._parse_rules()

    # -- rule parsing --------------------------------------------------

    def _iter_rule_items(self):
        defaults = set(self.service.config.defaults())
        for key, value in self._config.items():
            if key not in defaults:
                yield key, value

    def _parse_rules(self) -> None:
        items = list(self._iter_rule_items())
        if not items:
            self._fatal = "signal_action: no rules configured (missing or empty section)"
            return

        for name, raw in items:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                self._fatal = f"signal_action: rule '{name}' is not valid JSON: {exc}"
                return
            if not isinstance(data, dict):
                self.logger.warning(
                    "signal_action: rule '%s' must be a JSON object; disabled", name
                )
                continue
            rule = self._build_rule(name, data)
            if rule is not None:
                self._rules[name] = rule

    def _build_rule(self, name: str, data: dict) -> Rule | None:
        trigger_raw = data.get("trigger")
        action_raw = data.get("action")
        if not trigger_raw:
            self.logger.warning("signal_action: rule '%s' has no 'trigger'; disabled", name)
            return None
        if not action_raw:
            self.logger.warning("signal_action: rule '%s' has no 'action'; disabled", name)
            return None

        trigger = _parse_trigger(trigger_raw)
        if trigger is None:
            self.logger.warning(
                "signal_action: rule '%s' has invalid trigger '%s'; disabled",
                name,
                trigger_raw,
            )
            return None

        action = _parse_action(action_raw)
        if action is None:
            self.logger.warning(
                "signal_action: rule '%s' has invalid action '%s'; disabled",
                name,
                action_raw,
            )
            return None

        argmap_raw = data.get("argmap")
        if argmap_raw is None:
            argmap = None
        elif isinstance(argmap_raw, str):
            try:
                argmap = json.loads(argmap_raw)
            except (json.JSONDecodeError, TypeError):
                self.logger.warning(
                    "signal_action: rule '%s' has invalid argmap; disabled", name
                )
                return None
        else:
            argmap = list(argmap_raw)

        try:
            queue_size = max(1, int(data.get("queue_size", 1000)))
        except (TypeError, ValueError):
            queue_size = 1000

        return Rule(
            name=name,
            trigger=trigger,
            action=action,
            persistent=bool(data.get("persistent", True)),
            notify=bool(data.get("notify", False)),
            argmap=argmap,
            queue_size=queue_size,
        )

    # -- lifecycle -----------------------------------------------------

    async def setup(self) -> None:
        if self._fatal:
            raise RuntimeError(self._fatal)

        self._export_contract()

        for name, rule in self._rules.items():
            rule.queue = asyncio.Queue(maxsize=rule.queue_size)
            task = asyncio.create_task(self._dispatch_loop(rule), name=f"signal_action:{name}")
            self._dispatcher_tasks[name] = task

        await self._arm_all()

        if any(r.persistent for r in self._rules.values()):
            self._rearm_task = asyncio.create_task(
                self._rearm_loop(), name="signal_action:rearm"
            )

    async def teardown(self) -> None:
        if self._rearm_task is not None:
            self._rearm_task.cancel()
            self._rearm_task = None
        for task in self._dispatcher_tasks.values():
            task.cancel()
        self._dispatcher_tasks.clear()
        self._unsubscribe_all()

    def _export_contract(self) -> None:
        try:
            self._contract = SignalActionContract("org.pympacds.SignalAction", self)
            self.service.bus.add_interface("signal_action", self._contract)
        except Exception as exc:
            self.logger.warning("signal_action: could not export contract: %s", exc)
            self._contract = None

    # -- discovery helpers ---------------------------------------------

    def _resolve_friend(self, busname: str) -> str:
        bus = self.service.bus
        if busname in bus.friendbus:
            return busname
        matches = bus.query_friend_busname(busname)
        if matches:
            return matches[0]
        return busname

    async def _friends_by_tag(self, tag: str) -> list[str]:
        friends = self.service.bus.query_friend_busname("")
        result = []
        for busname in friends:
            try:
                tags = await self.service.bus.get_friend_tags(busname, "provides")
            except Exception:
                tags = []
            if tag in tags:
                result.append(busname)
        return result

    # -- signal source resolution (REQ-MIDW-006) -----------------------

    async def _resolve_trigger_sources(self, rule: Rule) -> list[tuple]:
        """Return ``[(busname, path, interface, arg_names), ...]``."""
        t = rule.trigger
        if t.kind == "tag":
            friends = await self._friends_by_tag(t.capability)
            iface_filter = None
        else:
            friends = [self._resolve_friend(t.busname)]
            iface_filter = t.interface

        sources = []
        for busname in friends:
            try:
                tree = await self.service.bus.introspect_tree(busname, self.service.bus._obj_root)
            except Exception:
                continue
            for path, bi in tree.items():
                for iface, sig in bi.find_signal(t.member):
                    if iface_filter is not None and iface != iface_filter:
                        continue
                    arg_names = [a.get("name", "") for a in sig.get("args", [])]
                    sources.append((busname, path, iface, arg_names))
        return sources

    # -- action target resolution (REQ-MIDW-007) -----------------------

    async def _resolve_action_targets(self, rule: Rule) -> list[tuple]:
        """Return ``[(busname, path, interface, method_dict), ...]``."""
        a = rule.action
        if a.kind == "tag":
            friends = await self._friends_by_tag(a.capability)
            targets = []
            for busname in friends:
                try:
                    tree = await self.service.bus.introspect_tree(
                        busname, self.service.bus._obj_root
                    )
                except Exception:
                    continue
                for path, bi in tree.items():
                    for iface, m in bi.find_method(a.method):
                        targets.append((busname, path, iface, m))
            return targets
        return [(self._resolve_friend(a.busname), a.object_path, a.interface, None)]

    # -- subscription management (REQ-MIDW-006/008) --------------------

    def _make_callback(self, rule: Rule, arg_names: list):
        def callback(*values):
            event = SignalEvent(
                list(values),
                dict(zip(arg_names, values)) if arg_names else {},
            )
            self._enqueue(rule, event)

        return callback

    async def _subscribe(self, rule, busname, path, interface, member, arg_names):
        try:
            proxy = await self.service.bus.get_interface(busname, path, interface)
        except Exception as exc:
            self.logger.warning(
                "signal_action: cannot get %s at %s (%s): %s",
                interface,
                path,
                busname,
                exc,
            )
            return None
        on = getattr(proxy, f"on_{member}", None)
        if on is None:
            self.logger.warning("signal_action: interface %s has no signal %s", interface, member)
            return None
        callback = self._make_callback(rule, arg_names)
        try:
            on(callback)
        except Exception as exc:
            self.logger.warning("signal_action: subscribe %s.%s failed: %s", interface, member, exc)
            return None
        key = (busname, path, interface, member)
        return (key, proxy, member, callback)

    def _unsubscribe_one(self, sub) -> None:
        _key, proxy, member, callback = sub
        off = getattr(proxy, f"off_{member}", None)
        if off is not None:
            try:
                off(callback)
            except Exception:
                pass

    def _unsubscribe_all(self) -> None:
        for rule in self._rules.values():
            for sub in rule.subscriptions:
                self._unsubscribe_one(sub)
            rule.subscriptions.clear()
            if rule.active:
                rule.active = False
                self._emit_rule_status(rule, False)

    async def _arm_all(self) -> None:
        for rule in self._rules.values():
            await self._arm_rule(rule)
            if not rule.persistent and not rule.subscriptions:
                self.logger.warning(
                    "signal_action: rule '%s' source unavailable; dropped", rule.name
                )

    async def _arm_rule(self, rule: Rule) -> None:
        sources = await self._resolve_trigger_sources(rule)
        expected = {(b, p, i, rule.trigger.member) for (b, p, i, _a) in sources}
        current = {sub[0] for sub in rule.subscriptions}

        for sub in list(rule.subscriptions):
            if sub[0] not in expected:
                self._unsubscribe_one(sub)
                rule.subscriptions.remove(sub)

        for busname, path, iface, arg_names in sources:
            key = (busname, path, iface, rule.trigger.member)
            if key not in current:
                sub = await self._subscribe(
                    rule, busname, path, iface, rule.trigger.member, arg_names
                )
                if sub is not None:
                    rule.subscriptions.append(sub)

        new_active = bool(rule.subscriptions)
        if new_active != rule.active:
            rule.active = new_active
            self._emit_rule_status(rule, new_active)

    # -- dispatch (REQ-MIDW-009/010) -----------------------------------

    def _enqueue(self, rule: Rule, event: SignalEvent) -> None:
        try:
            rule.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.logger.warning(
                "signal_action: rule '%s' queue overflow; dropping oldest", rule.name
            )
            try:
                rule.queue.get_nowait()
                rule.queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _dispatch_loop(self, rule: Rule) -> None:
        try:
            while True:
                event = await rule.queue.get()
                try:
                    await self._dispatch_signal(rule, event)
                except Exception:
                    self.logger.exception(
                        "signal_action: rule '%s' dispatch error", rule.name
                    )
        except asyncio.CancelledError:
            pass

    async def _dispatch_signal(self, rule: Rule, event: SignalEvent) -> None:
        targets = await self._resolve_action_targets(rule)
        if not targets:
            self.logger.warning(
                "signal_action: rule '%s' has no available target", rule.name
            )
            self._emit_completed(rule, {"error": "no target available"})
            if rule.persistent:
                self._add_pending(rule, event)
            return
        results = await asyncio.gather(
            *[self._perform_action(rule, target, event) for target in targets],
            return_exceptions=True,
        )
        if not any(r is True for r in results) and rule.persistent:
            self._add_pending(rule, event)

    async def _perform_action(self, rule: Rule, target, event: SignalEvent) -> bool:
        busname, path, interface, method_dict = target
        method = rule.action.method
        try:
            args = self._map_args(rule, event, method_dict)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.logger.warning("signal_action: rule '%s' argmap failed: %s", rule.name, exc)
            self._emit_completed(rule, {"error": f"argmap: {exc}"})
            return False
        try:
            proxy = await self.service.bus.get_interface(busname, path, interface)
            await getattr(proxy, method)(*args)
        except Exception as exc:
            self.logger.warning(
                "signal_action: rule '%s' -> %s.%s failed: %s",
                rule.name,
                interface,
                method,
                exc,
            )
            self._emit_completed(rule, {"error": str(exc)})
            return False
        self.logger.info("signal_action: rule '%s' -> %s.%s OK", rule.name, interface, method)
        self._emit_completed(rule, {"result": "ok"})
        return True

    # -- argument mapping (REQ-MIDW-009) -------------------------------

    def _map_args(self, rule: Rule, event: SignalEvent, method_dict) -> list:
        if rule.argmap is None:
            return [json.dumps(event.args)]
        target_args = method_dict["in"] if method_dict else []
        result = []
        for i, expr in enumerate(rule.argmap):
            resolved = self._resolve_expr(expr, event)
            arg_type = target_args[i]["type"] if i < len(target_args) else "s"
            result.append(self._coerce(resolved, arg_type))
        return result

    def _resolve_expr(self, expr: str, event: SignalEvent) -> str:
        def repl(match):
            key = match.group(1)
            if key.isdigit():
                idx = int(key)
                if 0 <= idx < len(event.args):
                    return self._stringify(event.args[idx])
                raise ValueError(f"unknown positional signal argument {{{key}}}")
            if key in event.by_name:
                return self._stringify(event.by_name[key])
            raise ValueError(f"unknown signal argument {{{key}}}")

        return re.sub(r"\{([^}]*)\}", repl, expr)

    @staticmethod
    def _stringify(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _coerce(value, arg_type: str):
        value = str(value)
        if arg_type == "b":
            return value.lower() in ("true", "1", "yes", "on")
        if arg_type in ("i", "n", "q", "x", "t", "u", "y"):
            return int(value)
        if arg_type == "d":
            return float(value)
        return value

    # -- retry / re-arming (REQ-MIDW-008/012) --------------------------

    def _add_pending(self, rule: Rule, event: SignalEvent) -> None:
        if len(rule.pending) >= rule.queue_size:
            rule.pending.pop(0)
        rule.pending.append(event)

    async def _rearm_loop(self) -> None:
        try:
            while True:
                await self.service.bus.wait_friend_changes()
                await self._rearm()
        except asyncio.CancelledError:
            pass

    async def _rearm(self) -> None:
        for rule in self._rules.values():
            if not rule.persistent:
                continue
            await self._arm_rule(rule)
            await self._retry_pending(rule)

    async def _retry_pending(self, rule: Rule) -> None:
        if not rule.pending:
            return
        pending = list(rule.pending)
        rule.pending.clear()
        for event in pending:
            await self._dispatch_signal(rule, event)

    # -- D-Bus status (REQ-MIDW-014) -----------------------------------

    def signal_action_get_rules(self) -> str:
        rules = []
        for name, rule in self._rules.items():
            rules.append(
                {
                    "name": name,
                    "trigger": rule.trigger.raw,
                    "action": rule.action.raw,
                    "persistent": rule.persistent,
                    "notify": rule.notify,
                    "argmap": rule.argmap,
                    "active": rule.active,
                }
            )
        return json.dumps(rules)

    def _emit_rule_status(self, rule: Rule, active: bool) -> None:
        if self._contract is not None:
            try:
                self._contract.rule_status_changed(rule.name, active)
            except Exception:
                pass

    def _emit_completed(self, rule: Rule, result: dict) -> None:
        if not rule.notify or self._contract is None:
            return
        try:
            self._contract.signal_action_completed(rule.name, json.dumps(result))
        except Exception:
            pass

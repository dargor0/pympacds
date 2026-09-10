"""Unit tests for the signal_action middleware (REQ-MIDW-004..014)."""

import asyncio
import json
import logging

import pytest

from pympacds.introspect import BusIntrospect
from pympacds.process import ProcessBase
from pympacds.signal_action import SignalActionMiddleware, SignalActionContract, SignalEvent


# ----------------------------------------------------------------------
# fakes
# ----------------------------------------------------------------------


class FakeProxy:
    """A proxy interface recording subscriptions and method calls."""

    def __init__(self, interface, fail=False, signals=None):
        self.interface = interface
        self.fail = fail
        self._signals = signals  # None => all signals available
        self.subscriptions = {}  # member -> [callbacks]
        self.calls = []  # (method, args)

    def __getattr__(self, name):
        if name.startswith("on_"):
            member = name[3:]

            def on(cb):
                if self._signals is not None and member not in self._signals:
                    return None
                self.subscriptions.setdefault(member, []).append(cb)
                return cb

            return on
        if name.startswith("off_"):
            member = name[4:]

            def off(cb):
                self.subscriptions.get(member, []).remove(cb)

            return off

        async def call(*args):
            self.calls.append((name, args))
            if self.fail:
                raise RuntimeError("call failed")
            return "ok"

        return call

    def emit(self, member, *args):
        for cb in list(self.subscriptions.get(member, [])):
            cb(*args)


class FakeBus:
    """Minimal DBusManager stand-in."""

    def __init__(self):
        self.friendbus = set()
        self._obj_root = "/org/pympacds"
        self.tags = {}  # busname -> {"provides": [...], "requires": [...]}
        self.trees = {}  # busname -> {path: BusIntrospect}
        self.proxies = {}  # (busname, path, iface) -> FakeProxy
        self.exported = []  # (path, iface)
        self.change_event = asyncio.Event()

    def query_friend_busname(self, query=""):
        if not query:
            return list(self.friendbus)
        return [n for n in self.friendbus if query in n]

    async def get_friend_tags(self, busname, kind="provides"):
        return list(self.tags.get(busname, {}).get(kind, []))

    async def introspect_tree(self, busname, pathname):
        return self.trees.get(busname, {})

    async def get_interface(self, busname, path, interface):
        key = (busname, path, interface)
        if key not in self.proxies:
            self.proxies[key] = FakeProxy(interface)
        return self.proxies[key]

    def add_interface(self, path, iface):
        self.exported.append((path, iface))

    async def wait_friend_changes(self):
        await self.change_event.wait()

    def signal_friend_change(self):
        self.change_event.set()
        self.change_event.clear()


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def make_mw(rules=None, bus=None, section="signal_rules"):
    p = ProcessBase("test", "1.0")
    p.logger.setLevel(logging.CRITICAL)
    if rules is not None:
        p.config[section] = rules
        mw = SignalActionMiddleware(p, section)
    else:
        mw = SignalActionMiddleware(p, None)
    if bus is not None:
        p.bus = bus
    return mw, p


def node(path, xml):
    return BusIntrospect(xml, path=path)


GPIO_XML = """<node>
  <interface name="org.pympacds.GPIO">
    <signal name="line_changed">
      <arg name="name" type="s"/>
      <arg name="offset" type="u"/>
      <arg name="value" type="b"/>
    </signal>
  </interface>
</node>"""

MQTT_XML = """<node>
  <interface name="org.pympacds.MQTT">
    <method name="publish">
      <arg name="topic" type="s" direction="in"/>
      <arg name="payload" type="s" direction="in"/>
    </method>
  </interface>
</node>"""


# ----------------------------------------------------------------------
# rule parsing & validation (REQ-MIDW-005)
# ----------------------------------------------------------------------


class TestRuleParsing:
    def test_tag_rule_defaults(self):
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}
        )
        assert "r1" in mw._rules
        rule = mw._rules["r1"]
        assert rule.trigger.kind == "tag"
        assert rule.trigger.capability == "gpio"
        assert rule.trigger.member == "line_changed"
        assert rule.action.kind == "tag"
        assert rule.action.capability == "mqtt"
        assert rule.action.method == "publish"
        assert rule.persistent is True
        assert rule.notify is False
        assert rule.argmap is None
        assert rule.queue_size == 1000

    def test_explicit_rule(self):
        mw, _ = make_mw(
            {
                "r1": json.dumps(
                    {
                        "trigger": "org.pympacds.http:org.pympacds.HTTP:request_completed",
                        "action": "org.pympacds.http:/org/pympacds/http:org.pympacds.HTTP:send",
                        "persistent": False,
                        "notify": True,
                        "queue_size": 5,
                    }
                )
            }
        )
        rule = mw._rules["r1"]
        assert rule.trigger.kind == "explicit"
        assert rule.trigger.busname == "org.pympacds.http"
        assert rule.trigger.interface == "org.pympacds.HTTP"
        assert rule.trigger.member == "request_completed"
        assert rule.action.kind == "explicit"
        assert rule.action.object_path == "/org/pympacds/http"
        assert rule.action.method == "send"
        assert rule.persistent is False
        assert rule.notify is True
        assert rule.queue_size == 5

    def test_missing_trigger_disabled(self):
        mw, _ = make_mw({"r1": json.dumps({"action": "@mqtt:publish"})})
        assert "r1" not in mw._rules

    def test_missing_action_disabled(self):
        mw, _ = make_mw({"r1": json.dumps({"trigger": "@gpio:line_changed"})})
        assert "r1" not in mw._rules

    def test_tag_action_without_method_disabled(self):
        mw, _ = make_mw({"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt"})})
        assert "r1" not in mw._rules

    def test_invalid_json_skipped(self):
        mw, _ = make_mw({"r1": "not json {"})
        assert "r1" not in mw._rules  # non-fatal: skipped

    def test_empty_section_no_rules(self):
        mw, _ = make_mw({})
        assert mw._rules == {}  # non-fatal: rules may be added in code (REQ-MIDW-016)

    def test_missing_section_no_rules(self):
        mw, _ = make_mw(None)
        assert mw._rules == {}

    @pytest.mark.asyncio
    async def test_setup_with_no_rules_succeeds(self):
        mw, _ = make_mw(None)
        await mw.setup()  # non-fatal: starts empty and waits for register_rule()
        assert mw._rules == {}

    @pytest.mark.asyncio
    async def test_setup_with_invalid_json_succeeds(self):
        mw, _ = make_mw({"r1": "bad json"})
        await mw.setup()  # non-fatal
        assert mw._rules == {}


# ----------------------------------------------------------------------
# tag resolution (REQ-MIDW-006/007)
# ----------------------------------------------------------------------


class TestResolution:
    @pytest.mark.asyncio
    async def test_friends_by_tag(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio", "org.pympacds.http"}
        bus.tags = {
            "org.pympacds.gpio": {"provides": ["gpio"]},
            "org.pympacds.http": {"provides": ["http"]},
        }
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@http:send"})}, bus
        )
        friends = await mw._friends_by_tag("gpio")
        assert friends == ["org.pympacds.gpio"]

    @pytest.mark.asyncio
    async def test_resolve_trigger_sources_tag(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio"}
        bus.tags = {"org.pympacds.gpio": {"provides": ["gpio"]}}
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)}
        }
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@http:send"})}, bus
        )
        sources = await mw._resolve_trigger_sources(mw._rules["r1"])
        assert sources == [
            (
                "org.pympacds.gpio",
                "/org/pympacds/gpio",
                "org.pympacds.GPIO",
                ["name", "offset", "value"],
            )
        ]

    @pytest.mark.asyncio
    async def test_resolve_trigger_sources_explicit(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio"}
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)}
        }
        rule = {
            "trigger": "org.pympacds.gpio:org.pympacds.GPIO:line_changed",
            "action": "@http:send",
        }
        mw, _ = make_mw({"r1": json.dumps(rule)}, bus)
        sources = await mw._resolve_trigger_sources(mw._rules["r1"])
        assert sources[0][0:3] == ("org.pympacds.gpio", "/org/pympacds/gpio", "org.pympacds.GPIO")

    @pytest.mark.asyncio
    async def test_resolve_action_targets_tag(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.mqtt"}
        bus.tags = {"org.pympacds.mqtt": {"provides": ["mqtt"]}}
        bus.trees = {
            "org.pympacds.mqtt": {"/org/pympacds/mqtt": node("/org/pympacds/mqtt", MQTT_XML)}
        }
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        targets = await mw._resolve_action_targets(mw._rules["r1"])
        assert targets[0][0:3] == ("org.pympacds.mqtt", "/org/pympacds/mqtt", "org.pympacds.MQTT")


# ----------------------------------------------------------------------
# signal -> action dispatch (REQ-MIDW-009/010)
# ----------------------------------------------------------------------


class TestDispatch:
    def _gpio_to_mqtt(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio", "org.pympacds.mqtt"}
        bus.tags = {
            "org.pympacds.gpio": {"provides": ["gpio"]},
            "org.pympacds.mqtt": {"provides": ["mqtt"]},
        }
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)},
            "org.pympacds.mqtt": {"/org/pympacds/mqtt": node("/org/pympacds/mqtt", MQTT_XML)},
        }
        return bus

    @pytest.mark.asyncio
    async def test_json_passthrough_action(self):
        bus = self._gpio_to_mqtt()
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        await mw.setup()
        try:
            rule = mw._rules["r1"]
            event = SignalEvent(["gpio0", 3, True], {})
            await mw._dispatch_signal(rule, event)
            proxy = bus.proxies[("org.pympacds.mqtt", "/org/pympacds/mqtt", "org.pympacds.MQTT")]
            assert len(proxy.calls) == 1
            method, args = proxy.calls[0]
            assert method == "publish"
            assert json.loads(args[0]) == ["gpio0", 3, True]
        finally:
            await mw.teardown()

    @pytest.mark.asyncio
    async def test_argmap_template(self):
        bus = self._gpio_to_mqtt()
        mw, _ = make_mw(
            {
                "r1": json.dumps(
                    {
                        "trigger": "@gpio:line_changed",
                        "action": "@mqtt:publish",
                        "argmap": '["sensors/{0}", "{2}"]',
                    }
                )
            },
            bus,
        )
        await mw.setup()
        try:
            rule = mw._rules["r1"]
            event = SignalEvent(["gpio0", 3, True], {})
            await mw._dispatch_signal(rule, event)
            proxy = bus.proxies[("org.pympacds.mqtt", "/org/pympacds/mqtt", "org.pympacds.MQTT")]
            method, args = proxy.calls[0]
            assert args == ("sensors/gpio0", "true")
        finally:
            await mw.teardown()

    @pytest.mark.asyncio
    async def test_argmap_by_name_and_coercion(self):
        bus = self._gpio_to_mqtt()
        mw, _ = make_mw(
            {
                "r1": json.dumps(
                    {
                        "trigger": "@gpio:line_changed",
                        "action": "@mqtt:publish",
                        "argmap": '["{name}", "{value}"]',
                    }
                )
            },
            bus,
        )
        rule = mw._rules["r1"]
        event = SignalEvent(["gpio0", 3, True], {"name": "gpio0", "offset": 3, "value": True})
        args = mw._map_args(
            rule, event, {"in": [{"name": "topic", "type": "s"}, {"name": "payload", "type": "b"}]}
        )
        assert args == ["gpio0", True]

    def test_coerce_bool(self):
        assert SignalActionMiddleware._coerce("true", "b") is True
        assert SignalActionMiddleware._coerce("0", "b") is False

    def test_coerce_int(self):
        assert SignalActionMiddleware._coerce("42", "i") == 42

    @pytest.mark.asyncio
    async def test_unavailable_target_persistent_pending(self):
        bus = FakeBus()
        bus.friendbus = set()
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        rule = mw._rules["r1"]
        event = SignalEvent([], {})
        await mw._dispatch_signal(rule, event)
        assert len(rule.pending) == 1

    @pytest.mark.asyncio
    async def test_unavailable_target_nonpersistent_no_pending(self):
        bus = FakeBus()
        bus.friendbus = set()
        mw, _ = make_mw(
            {
                "r1": json.dumps(
                    {
                        "trigger": "@gpio:line_changed",
                        "action": "@mqtt:publish",
                        "persistent": False,
                    }
                )
            },
            bus,
        )
        rule = mw._rules["r1"]
        event = SignalEvent([], {})
        await mw._dispatch_signal(rule, event)
        assert rule.pending == []


# ----------------------------------------------------------------------
# bounded queue overflow (REQ-MIDW-010)
# ----------------------------------------------------------------------


class TestQueue:
    def test_overflow_drops_oldest(self):
        bus = FakeBus()
        mw, _ = make_mw(
            {
                "r1": json.dumps(
                    {"trigger": "@gpio:line_changed", "action": "@mqtt:publish", "queue_size": 2}
                )
            },
            bus,
        )
        rule = mw._rules["r1"]
        rule.queue = asyncio.Queue(maxsize=2)
        ev1 = object()
        ev2 = object()
        ev3 = object()
        mw._enqueue(rule, ev1)
        mw._enqueue(rule, ev2)
        mw._enqueue(rule, ev3)  # drops oldest (ev1)
        assert rule.queue.get_nowait() is ev2
        assert rule.queue.get_nowait() is ev3


# ----------------------------------------------------------------------
# retry & re-arm (REQ-MIDW-008/011/012)
# ----------------------------------------------------------------------


class TestRetry:
    @pytest.mark.asyncio
    async def test_persistent_retries_pending_on_rearm(self):
        bus = FakeBus()
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        rule = mw._rules["r1"]
        event = SignalEvent([], {})
        rule.pending.append(event)

        # now a target appears
        bus.friendbus = {"org.pympacds.mqtt"}
        bus.tags = {"org.pympacds.mqtt": {"provides": ["mqtt"]}}
        bus.trees = {
            "org.pympacds.mqtt": {"/org/pympacds/mqtt": node("/org/pympacds/mqtt", MQTT_XML)}
        }

        await mw._retry_pending(rule)
        assert rule.pending == []
        proxy = bus.proxies[("org.pympacds.mqtt", "/org/pympacds/mqtt", "org.pympacds.MQTT")]
        assert len(proxy.calls) == 1


class TestRearm:
    @pytest.mark.asyncio
    async def test_persistent_subscribes_when_peer_appears(self):
        bus = FakeBus()
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        rule = mw._rules["r1"]
        rule.queue = asyncio.Queue()

        # no peer -> inactive
        await mw._arm_rule(rule)
        assert rule.active is False

        # peer appears
        bus.friendbus = {"org.pympacds.gpio"}
        bus.tags = {"org.pympacds.gpio": {"provides": ["gpio"]}}
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)}
        }
        await mw._arm_rule(rule)
        assert rule.active is True
        assert len(rule.subscriptions) == 1

        # peer disappears
        bus.friendbus = set()
        bus.trees = {}
        await mw._arm_rule(rule)
        assert rule.active is False
        assert rule.subscriptions == []


# ----------------------------------------------------------------------
# D-Bus export (REQ-MIDW-014)
# ----------------------------------------------------------------------


class TestExport:
    def test_get_rules_json(self):
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}
        )
        rules = json.loads(mw.signal_action_get_rules())
        assert len(rules) == 1
        assert rules[0]["name"] == "r1"
        assert rules[0]["trigger"] == "@gpio:line_changed"
        assert rules[0]["active"] is False

    @pytest.mark.asyncio
    async def test_contract_exported_on_setup(self):
        bus = FakeBus()
        mw, _ = make_mw(
            {"r1": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})}, bus
        )
        await mw.setup()
        try:
            assert bus.exported
            path, iface = bus.exported[0]
            assert isinstance(iface, SignalActionContract)
        finally:
            await mw.teardown()


class TestProgrammaticRegistration:
    def _bus(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio", "org.pympacds.mqtt"}
        bus.tags = {
            "org.pympacds.gpio": {"provides": ["gpio"]},
            "org.pympacds.mqtt": {"provides": ["mqtt"]},
        }
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)},
            "org.pympacds.mqtt": {"/org/pympacds/mqtt": node("/org/pympacds/mqtt", MQTT_XML)},
        }
        return bus

    @pytest.mark.asyncio
    async def test_register_valid_rule(self):
        bus = self._bus()
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("gpio_to_mqtt", "@gpio:line_changed", "@mqtt:publish")
        assert ok is True
        rule = mw._rules["gpio_to_mqtt"]
        assert rule.persistent is True
        assert rule.notify is False
        assert rule.argmap is None
        assert rule.queue_size == 1000

    @pytest.mark.asyncio
    async def test_register_invalid_trigger(self):
        bus = self._bus()
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("bad", "not-a-valid-spec", "@mqtt:publish")
        assert ok is False
        assert "bad" not in mw._rules

    @pytest.mark.asyncio
    async def test_register_tag_action_without_method(self):
        bus = self._bus()
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("bad", "@gpio:line_changed", "@mqtt")
        assert ok is False
        assert "bad" not in mw._rules

    @pytest.mark.asyncio
    async def test_register_unresolvable_trigger_persistent_fails(self):
        bus = FakeBus()  # empty friend set
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("r", "@gpio:line_changed", "@mqtt:publish", persistent=True)
        assert ok is False
        assert "r" not in mw._rules

    @pytest.mark.asyncio
    async def test_register_unresolvable_action_fails(self):
        bus = FakeBus()
        bus.friendbus = {"org.pympacds.gpio"}
        bus.tags = {"org.pympacds.gpio": {"provides": ["gpio"]}}
        bus.trees = {
            "org.pympacds.gpio": {"/org/pympacds/gpio": node("/org/pympacds/gpio", GPIO_XML)}
        }
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("r", "@gpio:line_changed", "@mqtt:publish")
        assert ok is False  # no mqtt friend -> action not resolvable
        assert "r" not in mw._rules

    @pytest.mark.asyncio
    async def test_register_name_collision(self):
        bus = self._bus()
        mw, _ = make_mw(
            {"r": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})},
            bus,
        )
        ok = await mw.register_rule("r", "@gpio:line_changed", "@mqtt:publish")
        assert ok is False

    @pytest.mark.asyncio
    async def test_register_before_setup_is_staged_then_armed(self):
        bus = self._bus()
        mw, _ = make_mw(None, bus)
        ok = await mw.register_rule("r", "@gpio:line_changed", "@mqtt:publish")
        assert ok is True
        assert mw._rules["r"].queue is None  # not yet armed
        await mw.setup()
        try:
            assert mw._setup_done is True
            assert mw._rules["r"].active is True
            assert len(mw._rules["r"].subscriptions) == 1
        finally:
            await mw.teardown()

    @pytest.mark.asyncio
    async def test_register_after_setup_arms_immediately(self):
        bus = self._bus()
        mw, _ = make_mw(
            {"seed": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})},
            bus,
        )
        await mw.setup()
        try:
            ok = await mw.register_rule("runtime", "@gpio:line_changed", "@mqtt:publish")
            assert ok is True
            rule = mw._rules["runtime"]
            assert rule.queue is not None
            assert rule.active is True
        finally:
            await mw.teardown()

    @pytest.mark.asyncio
    async def test_remove_rule(self):
        bus = self._bus()
        mw, _ = make_mw(
            {"r": json.dumps({"trigger": "@gpio:line_changed", "action": "@mqtt:publish"})},
            bus,
        )
        await mw.setup()
        try:
            assert "r" in mw._rules
            ok = await mw.remove_rule("r")
            assert ok is True
            assert "r" not in mw._rules
        finally:
            await mw.teardown()

    @pytest.mark.asyncio
    async def test_remove_unknown_rule(self):
        bus = self._bus()
        mw, _ = make_mw(None, bus)
        assert await mw.remove_rule("nope") is False

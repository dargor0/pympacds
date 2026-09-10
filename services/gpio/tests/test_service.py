"""Service-level unit tests (REQ-GPIO-003..007)."""

import asyncio
import configparser
import json
import logging

import pytest
from helpers import FakeShim, make_config

from pympacds_gpio.service import GpioService

logger = logging.getLogger("test_service")
logger.setLevel(logging.CRITICAL)


def _svc(**gpio_kv):
    svc = GpioService()
    svc.logger.setLevel(logging.CRITICAL)
    svc.config["gpio"] = make_config(**gpio_kv)
    return svc


def _prepared(fake_shim, linemap=None, **gpio_kv):
    svc = GpioService(shim_factory=lambda: fake_shim)
    svc.logger.setLevel(logging.CRITICAL)
    svc.config["gpio"] = make_config(**gpio_kv)
    if linemap is not None:
        svc.config["gpio.linemap"] = linemap
    svc._shim = fake_shim
    svc._parse_linemap()
    assert svc._resolve_line_sets() is True
    assert svc._parse_default_values() is True
    return svc


class FakeContract:
    def __init__(self):
        self.changed = []

    def line_changed(self, name, offset, value):
        self.changed.append((name, offset, value))


class TestParsing:
    def test_parse_line_list(self):
        assert GpioService._parse_line_list(" a , b ,, ") == ["a", "b"]

    def test_parse_line_list_empty(self):
        assert GpioService._parse_line_list("") == []

    def test_resolve_reference_number(self):
        assert _svc()._resolve_reference("7") == 7

    def test_resolve_reference_name(self):
        svc = _svc()
        svc._linemap = {"btn": 0}
        assert svc._resolve_reference("btn") == 0

    def test_resolve_reference_unresolvable(self):
        assert _svc()._resolve_reference("bogus") is None

    def test_resolve_reference_empty(self):
        assert _svc()._resolve_reference("") is None


class TestLinemap:
    def test_parse_linemap(self):
        svc = _svc()
        svc.config["gpio.linemap"] = {"btn": "0", "relay": "5"}
        svc._parse_linemap()
        assert svc._linemap == {"btn": 0, "relay": 5}
        assert svc._offset_to_name == {0: "btn", 5: "relay"}

    def test_linemap_name_beginning_with_digit_ignored(self):
        svc = _svc()
        svc.config["gpio.linemap"] = {"1bad": "3", "ok": "4"}
        svc._parse_linemap()
        assert "1bad" not in svc._linemap
        assert svc._linemap == {"ok": 4}

    def test_linemap_non_integer_offset_ignored(self):
        svc = _svc()
        svc.config["gpio.linemap"] = {"btn": "abc", "ok": "2"}
        svc._parse_linemap()
        assert "btn" not in svc._linemap
        assert svc._linemap == {"ok": 2}

    def test_no_linemap_section(self):
        svc = _svc()
        svc._parse_linemap()
        assert svc._linemap == {}


class TestLineSets:
    def test_effective_set_union(self):
        svc = _svc(lines="0,1", watch_lines="2", output_lines="3")
        assert svc._resolve_line_sets() is True
        assert svc._input_offsets == [0, 1]
        assert svc._watch_offsets == [2]
        assert svc._output_offsets == [3]
        assert svc._all_offsets == [0, 1, 2, 3]

    def test_watch_and_output_overlap(self):
        svc = _svc(lines="0", watch_lines="0,1", output_lines="1,2")
        assert svc._resolve_line_sets() is True
        assert svc._output_offsets == [1, 2]
        assert svc._watch_offsets == [0]  # 1 is output, so not watched
        assert svc._input_offsets == []  # 0 is watched
        assert svc._all_offsets == [0, 1, 2]

    def test_unresolvable_line_fails(self):
        svc = _svc(lines="nope")
        assert svc._resolve_line_sets() is False


class TestDefaultValues:
    def test_parse_default_values(self):
        svc = _prepared(
            FakeShim(),
            linemap={"relay": "3"},
            output_lines="relay,5",
            default_values="relay=1,5=0",
        )
        assert svc._default_values == {3: True, 5: False}

    def test_unresolvable_default_fails(self):
        svc = _svc(output_lines="3", default_values="nope=1")
        svc._parse_linemap()
        svc._resolve_line_sets()
        assert svc._parse_default_values() is False

    def test_non_output_default_ignored(self):
        svc = _svc(output_lines="3", default_values="9=1")
        svc._parse_linemap()
        assert svc._resolve_line_sets() is True
        assert svc._parse_default_values() is True
        assert svc._default_values == {}

    def test_empty_default_values(self):
        svc = _svc()
        assert svc._parse_default_values() is True
        assert svc._default_values == {}


class TestSetup:
    def _write_config(self, tmp_path, **gpio_kv):
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        cp["dbus"] = {"bus_prefix": "org.pympacds", "contract_health": "true"}
        cp["gpio"] = make_config(**gpio_kv)
        path = tmp_path / "gpio.ini"
        with open(path, "w") as f:
            cp.write(f)
        return str(path)

    def test_setup_success(self, fake_shim, tmp_path):
        svc = GpioService(shim_factory=lambda: fake_shim)
        svc.logger.setLevel(logging.CRITICAL)
        cfg = self._write_config(tmp_path, lines="0", watch_lines="1", output_lines="3")
        assert svc.setup(["-c", cfg]) is True
        assert fake_shim.opened is True
        assert fake_shim.requested["input"] == [0]
        assert fake_shim.requested["watch"] == [1]
        assert fake_shim.requested["output"] == [3]

    def test_setup_fails_on_output_capability(self, tmp_path):
        class BadShim(FakeShim):
            def request_lines(self, *args, **kwargs):
                return False

        shim = BadShim()
        svc = GpioService(shim_factory=lambda: shim)
        svc.logger.setLevel(logging.CRITICAL)
        cfg = self._write_config(tmp_path, output_lines="3")
        assert svc.setup(["-c", cfg]) is False

    def test_setup_passes_edge_debounce_active_low(self, fake_shim, tmp_path):
        svc = GpioService(shim_factory=lambda: fake_shim)
        svc.logger.setLevel(logging.CRITICAL)
        cfg = self._write_config(
            tmp_path, watch_lines="1", edge="rising", debounce_us="50", active_low="true"
        )
        assert svc.setup(["-c", cfg]) is True
        assert fake_shim.requested["edge"] == "rising"
        assert fake_shim.requested["debounce_us"] == 50
        assert fake_shim.requested["active_low"] is True


class TestCallbacks:
    def test_get_value(self, fake_shim):
        svc = _prepared(fake_shim, lines="0")
        fake_shim._values[0] = True
        assert svc.dbus_gpio_get_value("0") is True

    def test_get_value_by_name(self, fake_shim):
        svc = _prepared(fake_shim, linemap={"btn": "0"}, lines="btn")
        fake_shim._values[0] = True
        assert svc.dbus_gpio_get_value("btn") is True

    def test_unknown_line_raises(self, fake_shim):
        svc = _prepared(fake_shim, lines="0")
        with pytest.raises(ValueError):
            svc.dbus_gpio_get_value("99")

    def test_set_value(self, fake_shim):
        svc = _prepared(fake_shim, output_lines="3")
        assert svc.dbus_gpio_set_value("3", True) is True
        assert fake_shim._values[3] is True

    def test_set_value_on_non_output_raises(self, fake_shim):
        svc = _prepared(fake_shim, lines="0")
        with pytest.raises(ValueError):
            svc.dbus_gpio_set_value("0", True)

    def test_get_set_default_value(self, fake_shim):
        svc = _prepared(fake_shim, output_lines="3")
        assert svc.dbus_gpio_get_default_value("3") is False
        assert svc.dbus_gpio_set_default_value("3", True) is True
        assert svc.dbus_gpio_get_default_value("3") is True

    def test_get_default_value_on_non_output_raises(self, fake_shim):
        svc = _prepared(fake_shim, lines="0")
        with pytest.raises(ValueError):
            svc.dbus_gpio_get_default_value("0")

    def test_get_lines(self, fake_shim):
        svc = _prepared(fake_shim, linemap={"btn": "0"}, lines="btn,5")
        fake_shim._values[0] = True
        fake_shim._values[5] = False
        assert json.loads(svc.dbus_gpio_get_lines()) == {"btn": True, "5": False}

    def test_status(self, fake_shim):
        svc = _prepared(fake_shim, linemap={"relay": "3"}, output_lines="relay", watch_lines="0")
        fake_shim._values[0] = True
        data = json.loads(svc.dbus_gpio_status())
        assert data["chip"] == "/dev/gpiochip0"
        assert data["lines"] == ["0", "relay"]
        assert data["watch_lines"] == ["0"]
        assert data["output_lines"] == ["relay"]
        assert data["values"] == {"0": True, "relay": False}

    def test_watched_lines(self, fake_shim):
        svc = _prepared(fake_shim, linemap={"btn": "0"}, watch_lines="btn,5")
        assert svc.dbus_gpio_watched_lines() == ["btn", "5"]


class TestWatchLoop:
    def test_emit_line_changed_named(self, fake_shim):
        svc = _prepared(fake_shim, linemap={"btn": "0"}, watch_lines="btn")
        svc._contract = FakeContract()
        svc._emit_line_changed(0, True)
        assert svc._contract.changed == [("btn", 0, True)]

    def test_emit_line_changed_unmapped(self, fake_shim):
        svc = _prepared(fake_shim, watch_lines="0")
        svc._contract = FakeContract()
        svc._emit_line_changed(0, False)
        assert svc._contract.changed == [("", 0, False)]

    @pytest.mark.asyncio
    async def test_watch_loop_emits_events(self, fake_shim):
        svc = _prepared(fake_shim, watch_lines="0")
        svc._contract = FakeContract()
        fake_shim._events.append((0, True))
        task = asyncio.create_task(svc._watch_loop())
        await asyncio.sleep(0.03)
        task.cancel()
        await task
        assert svc._contract.changed == [("", 0, True)]

    @pytest.mark.asyncio
    async def test_watch_loop_does_not_stall_event_loop(self, fake_shim):
        svc = _prepared(fake_shim, watch_lines="0")
        svc._contract = FakeContract()

        ticks = []

        async def ticker():
            for _ in range(5):
                ticks.append(1)
                await asyncio.sleep(0.001)

        watch = asyncio.create_task(svc._watch_loop())
        ticker_task = asyncio.create_task(ticker())
        await ticker_task
        assert len(ticks) == 5  # the loop kept ticking while watching
        watch.cancel()
        await watch


class TestCloseLoop:
    @pytest.mark.asyncio
    async def test_close_loop_releases_shim(self, fake_shim):
        svc = _prepared(fake_shim, output_lines="3")
        await svc.close_loop()
        assert fake_shim.released is True


class TestConfig:
    def test_get_set(self, fake_shim):
        svc = _prepared(fake_shim)
        assert svc.dbus_config_set("gpio", "chip", "/dev/gpiochip1") is True
        assert svc.dbus_config_get("gpio", "chip") == "/dev/gpiochip1"

    def test_get_missing_section(self, fake_shim):
        svc = _prepared(fake_shim)
        assert svc.dbus_config_get("nope", "key") == ""


class TestHealth:
    def test_ping(self, fake_shim):
        svc = _prepared(fake_shim)
        assert svc.dbus_health_ping() is True

    def test_status_includes_tags(self, fake_shim):
        svc = _prepared(fake_shim)
        data = json.loads(svc.dbus_health_status())
        assert "provides" in data
        assert "requires" in data

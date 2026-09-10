"""GPIO server service (REQ-GPIO-001..007)."""

import asyncio
import configparser
import json
import time
from typing import Any

from pympacds.builtin_contracts import ConfigContract, HealthContract
from pympacds.dbus import DBusManager
from pympacds.process import ProcessBase

from .contracts import GpioContract
from .shim import GpioShim


class GpioService(ProcessBase):
    """Monitors GPIO line state and edge events, exposed over D-Bus.

    Args:
        shim_factory: Optional callable ``f() -> shim`` returning a ``GpioShim``
            (or compatible), used by tests to inject a fake (no hardware).
    """

    def __init__(self, shim_factory=None):
        super().__init__(
            name="gpio",
            version="0.1.0",
            description="GPIO server service for pympacds",
        )
        self.bus: Any = None  # DBusManager, created in start_dbus()
        self._start_time = time.time()
        self._shim_factory = shim_factory
        self._shim = None
        self._contract = None
        self._health_contract = None
        self._config_contract = None

        self._linemap: dict[str, int] = {}
        self._offset_to_name: dict[int, str] = {}
        self._input_offsets: list[int] = []
        self._watch_offsets: list[int] = []
        self._output_offsets: list[int] = []
        self._all_offsets: list[int] = []
        self._default_values: dict[int, bool] = {}
        self._watch_task: asyncio.Task | None = None

    # -- config helpers -------------------------------------------------

    def _cfg(self, key: str, default):
        try:
            return self.config["gpio"].get(key, default)
        except (KeyError, configparser.Error):
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return str(self._cfg(key, default)).lower() in ("true", "1", "yes", "on")

    def _dbus_flag(self, key: str, default: bool) -> bool:
        if not self.config.has_section("dbus"):
            return default
        try:
            return self.config["dbus"].getboolean(key, default)
        except ValueError:
            return default

    # -- synchronous setup ----------------------------------------------

    def setup(self, inputargs: list[str] | None = None) -> bool:
        """Parse config, resolve the effective line set, and open the chip."""
        if not super().setup(inputargs):
            return False
        self._parse_linemap()
        if not self._resolve_line_sets():
            return False
        if not self._parse_default_values():
            return False

        self._shim = (
            self._shim_factory()
            if self._shim_factory
            else GpioShim(self.config["gpio"], self.logger)
        )
        if not self._shim.open():
            return False
        if not self._shim.request_lines(
            self._input_offsets,
            self._watch_offsets,
            self._output_offsets,
            self._cfg("edge", "both"),
            self._cfg_int("debounce_us", 0),
            self._cfg_bool("active_low", False),
            self._default_values,
        ):
            self.logger.error("gpio: failed to request lines (check output_lines capability)")
            return False
        return True

    # -- line-spec parsing (REQ-GPIO-003/004) ---------------------------

    def _parse_linemap(self) -> None:
        """Parse ``[gpio.linemap]`` into name→offset (and reverse) maps."""
        self._linemap = {}
        self._offset_to_name = {}
        if not self.config.has_section("gpio.linemap"):
            return
        for name, value in self.config["gpio.linemap"].items():
            name = name.strip()
            if not name:
                continue
            if name[0].isdigit():
                self.logger.warning(
                    "gpio: linemap name '%s' must not begin with a digit; ignored", name
                )
                continue
            try:
                offset = int(value)
            except (TypeError, ValueError):
                self.logger.warning(
                    "gpio: linemap '%s' has non-integer offset '%s'; ignored", name, value
                )
                continue
            self._linemap[name] = offset
            self._offset_to_name[offset] = name

    @staticmethod
    def _parse_line_list(raw: str) -> list[str]:
        return [t.strip() for t in raw.split(",") if t.strip()]

    def _resolve_reference(self, ref: str) -> int | None:
        """Resolve a name-or-offset reference — number first, then name."""
        ref = ref.strip()
        if not ref:
            return None
        try:
            return int(ref)
        except ValueError:
            return self._linemap.get(ref)

    def _resolve_line_sets(self) -> bool:
        lines = self._parse_line_list(self._cfg("lines", ""))
        watches = self._parse_line_list(self._cfg("watch_lines", ""))
        outputs = self._parse_line_list(self._cfg("output_lines", ""))

        def resolve(refs, kind):
            out = []
            for ref in refs:
                off = self._resolve_reference(ref)
                if off is None:
                    self.logger.error("gpio: cannot resolve %s line '%s'", kind, ref)
                    return None
                out.append(off)
            return out

        line_offsets = resolve(lines, "line")
        watch_offsets = resolve(watches, "watch")
        output_offsets = resolve(outputs, "output")
        if line_offsets is None or watch_offsets is None or output_offsets is None:
            return False

        line_set = list(dict.fromkeys(line_offsets))
        watch_set = list(dict.fromkeys(watch_offsets))
        output_set = list(dict.fromkeys(output_offsets))

        self._output_offsets = list(output_set)
        self._watch_offsets = [o for o in watch_set if o not in output_set]
        self._input_offsets = [o for o in line_set if o not in watch_set and o not in output_set]
        self._all_offsets = list(dict.fromkeys(line_set + watch_set + output_set))
        return True

    def _parse_default_values(self) -> bool:
        """Parse ``line=value`` pairs from ``default_values`` (outputs only)."""
        self._default_values = {}
        raw = self._cfg("default_values", "")
        if not raw.strip():
            return True
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                self.logger.error("gpio: default_values entry '%s' must be line=value", pair)
                return False
            ref, _, val = pair.partition("=")
            off = self._resolve_reference(ref)
            if off is None:
                self.logger.error("gpio: default_values cannot resolve line '%s'", ref)
                return False
            if off not in self._output_offsets:
                self.logger.warning("gpio: default_values line '%s' is not an output; ignored", ref)
                continue
            self._default_values[off] = val.strip().lower() in ("1", "true", "yes", "on")
        return True

    # -- D-Bus setup ----------------------------------------------------

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="gpio",
            bus_prefix=self.bus_prefix,
        )

        self._contract = GpioContract(f"{self.bus_prefix}.GPIO", self)
        self.bus.add_interface("gpio", self._contract)

        if self._dbus_flag("contract_health", True):
            self._health_contract = HealthContract(f"{self.bus_prefix}.Health", self)
            self.bus.add_interface("health", self._health_contract)

        if self._dbus_flag("contract_config", False):
            self._config_contract = ConfigContract(f"{self.bus_prefix}.Config", self)
            self.bus.add_interface("config", self._config_contract)

        await self.bus.start()

        if self._watch_offsets:
            self._watch_task = asyncio.create_task(self._watch_loop(), name="gpio_watch")
        return True

    # -- D-Bus contract base callbacks (REQ-GPIO-005) -------------------

    def _resolve_or_raise(self, line: str) -> int:
        off = self._resolve_reference(line)
        if off is None or off not in self._all_offsets:
            self.logger.warning("gpio: unknown line '%s'", line)
            raise ValueError(f"unknown line: {line}")
        return off

    def _display_name(self, offset: int) -> str:
        return self._offset_to_name.get(offset, str(offset))

    def dbus_gpio_get_value(self, line: str) -> bool:
        return self._shim.get_value(self._resolve_or_raise(line))

    def dbus_gpio_set_value(self, line: str, value: bool) -> bool:
        off = self._resolve_or_raise(line)
        if off not in self._output_offsets:
            self.logger.warning("gpio: line '%s' is not an output", line)
            raise ValueError(f"line '{line}' is not an output")
        return self._shim.set_value(off, bool(value))

    def dbus_gpio_get_default_value(self, line: str) -> bool:
        off = self._resolve_or_raise(line)
        if off not in self._output_offsets:
            raise ValueError(f"line '{line}' is not an output")
        return self._default_values.get(off, False)

    def dbus_gpio_set_default_value(self, line: str, value: bool) -> bool:
        off = self._resolve_or_raise(line)
        if off not in self._output_offsets:
            raise ValueError(f"line '{line}' is not an output")
        self._default_values[off] = bool(value)
        return True

    def dbus_gpio_get_lines(self) -> str:
        return json.dumps(self._read_all())

    def _read_all(self) -> dict[str, bool]:
        return {self._display_name(off): self._shim.get_value(off) for off in self._all_offsets}

    def dbus_gpio_status(self) -> str:
        return json.dumps(
            {
                "chip": self._shim.chip_path(),
                "lines": [self._display_name(o) for o in self._all_offsets],
                "watch_lines": [self._display_name(o) for o in self._watch_offsets],
                "output_lines": [self._display_name(o) for o in self._output_offsets],
                "values": self._read_all(),
            }
        )

    def dbus_gpio_watched_lines(self) -> list[str]:
        return [self._display_name(o) for o in self._watch_offsets]

    # -- edge events (REQ-GPIO-006) -------------------------------------

    async def _watch_loop(self) -> None:
        try:
            while True:
                events = self._shim.wait_events(timeout_s=0.05)
                for offset, value in events:
                    self._emit_line_changed(offset, value)
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    def _emit_line_changed(self, offset: int, value: bool) -> None:
        if self._contract is not None:
            name = self._offset_to_name.get(offset, "")
            self._contract.line_changed(name, offset, value)

    # -- graceful shutdown (REQ-GPIO-007) -------------------------------

    async def close_loop(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        if self._shim is not None:
            for off in self._output_offsets:
                try:
                    self._shim.set_value(off, self._default_values.get(off, False))
                except Exception:  # noqa: BLE001
                    pass
            self._shim.release()
        await super().close_loop()

    # -- health / config contract base callbacks (REQ-XCUT-004) --------

    def dbus_health_ping(self) -> bool:
        return True

    def dbus_health_status(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "uptime": self.dbus_health_get_uptime(),
                "provides": self.dbus_health_get_provides(),
                "requires": self.dbus_health_get_requires(),
            }
        )

    def dbus_health_get_uptime(self) -> int:
        return int(time.time() - self._start_time)

    def dbus_config_get(self, section: str, key: str) -> str:
        try:
            return self.config[section][key]
        except (KeyError, configparser.Error):
            return ""

    def dbus_config_set(self, section: str, key: str, value: str) -> bool:
        try:
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config[section][key] = value
        except Exception:  # noqa: BLE001
            return False
        if self._config_contract is not None:
            try:
                self._config_contract.config_changed(section, key, value)
            except Exception:  # noqa: BLE001
                pass
        return True


def main() -> None:
    GpioService().start()


if __name__ == "__main__":
    main()

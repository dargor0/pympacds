"""GPIO shim — adapter over the libgpiod v2 Python bindings (REQ-GPIO-002..007).

The shim is the only component that imports ``gpiod``.  It adapts libgpiod's
blocking C calls to the framework's single-asyncio-loop model:

- chip open, line requests, and value reads/writes are fast synchronous ioctls
  performed at setup time;
- the blocking ``wait_edge_events`` is polled with a short timeout from the
  service's periodic watch task, so the loop is never stalled (REQ-GPIO-006).

Debounce is delegated to the kernel: ``debounce_us`` is passed through to the
libgpiod line request (``LineSettings.debounce_period``); no software debounce
is implemented (REQ-GPIO-006).
"""

import glob
import logging
from typing import Any

try:
    import gpiod

    _EDGE_MAP = {
        "rising": gpiod.line.Edge.RISING,
        "falling": gpiod.line.Edge.FALLING,
        "both": gpiod.line.Edge.BOTH,
    }
except ImportError:  # gpiod is a declared dependency of the service
    gpiod = None  # type: ignore[assignment]
    _EDGE_MAP = {}


class GpioShim:
    """Adapter over a libgpiod v2 chip.

    Args:
        config: The ``[gpio]`` INI section (dict-like).
        logger: Logger for operational messages.
        chip_factory: Optional callable ``f(chip_path) -> chip`` returning a
            chip-like object, used by tests to inject a fake (no hardware).
    """

    def __init__(self, config, logger: logging.Logger, chip_factory=None):
        self._config = config
        self.logger = logger
        self._chip_factory = chip_factory
        self._chip: Any = None
        self._lines: Any = None
        self._chip_path = ""

    # -- config helpers -------------------------------------------------

    def _cfg(self, key, default):
        try:
            return self._config.get(key, default)
        except AttributeError:
            return default

    def _cfg_int(self, key, default):
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key, default):
        return str(self._cfg(key, default)).lower() in ("true", "1", "yes", "on")

    # -- chip open ------------------------------------------------------

    def open(self) -> bool:
        """Open the chip (auto-detect when ``chip`` is empty)."""
        chip = self._cfg("chip", "").strip()
        try:
            if self._chip_factory is not None:
                self._chip = self._chip_factory(chip)
                self._chip_path = chip or getattr(self._chip, "path", "")
                return True
            if gpiod is None:
                self.logger.error("gpio: gpiod is not installed")
                return False
            if not chip:
                chip = self._autodetect_chip()
            self._chip = gpiod.Chip(chip)
            self._chip_path = chip
        except OSError as exc:
            self.logger.error("gpio: cannot open chip '%s': %s", chip, exc)
            return False
        return True

    @staticmethod
    def _autodetect_chip() -> str:
        matches = sorted(glob.glob("/dev/gpiochip*"))
        return matches[0] if matches else "/dev/gpiochip0"

    def chip_path(self) -> str:
        return self._chip_path

    # -- line request ---------------------------------------------------

    def request_lines(
        self,
        input_offsets,
        watch_offsets,
        output_offsets,
        edge,
        debounce_us,
        active_low,
        defaults,
    ) -> bool:
        """Request the effective line set.  Returns ``False`` if the request
        fails (e.g. an output line lacks output capability)."""
        if self._chip_factory is not None:
            ok = self._chip.request_lines(
                input_offsets=list(input_offsets),
                watch_offsets=list(watch_offsets),
                output_offsets=list(output_offsets),
                edge=edge,
                debounce_us=debounce_us,
                active_low=bool(active_low),
                defaults=dict(defaults),
            )
            if not ok:
                self.logger.error("gpio: line request rejected (output capability?)")
                return False
            self._lines = self._chip
            return True

        config = {}
        for off in input_offsets:
            config[off] = gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)
        for off in watch_offsets:
            settings = gpiod.LineSettings(
                direction=gpiod.line.Direction.INPUT,
                edge_detection=_EDGE_MAP.get(edge, gpiod.line.Edge.BOTH),
                active_low=bool(active_low),
            )
            if debounce_us > 0:
                settings.debounce_period = debounce_us
            config[off] = settings
        for off in output_offsets:
            config[off] = gpiod.LineSettings(
                direction=gpiod.line.Direction.OUTPUT,
                output_value=(
                    gpiod.line.Value.ACTIVE if defaults.get(off) else gpiod.line.Value.INACTIVE
                ),
            )
        try:
            self._lines = gpiod.request_lines(self._chip, config=config, consumer="pympacds-gpio")
        except OSError as exc:
            self.logger.error("gpio: cannot request lines: %s", exc)
            return False
        return True

    # -- value access ---------------------------------------------------

    def get_value(self, offset: int) -> bool:
        return bool(self._lines.get_value(offset))

    def set_value(self, offset: int, value: bool) -> bool:
        try:
            self._lines.set_value(offset, bool(value))
        except OSError:
            return False
        return True

    # -- edge events (blocking; polled with a short timeout) ------------

    def wait_events(self, timeout_s: float) -> list[tuple[int, bool]]:
        """Block for up to ``timeout_s``; return ``(offset, value)`` pairs."""
        events = self._lines.wait_edge_events(timeout=timeout_s)
        result = []
        for ev in events:
            off = int(ev.line_offset)
            result.append((off, self.get_value(off)))
        return result

    # -- teardown -------------------------------------------------------

    def release(self) -> None:
        """Release the line request and close the chip (REQ-GPIO-007)."""
        if self._lines is not None:
            try:
                self._lines.release()
            except Exception:  # noqa: BLE001
                pass
            self._lines = None
        if self._chip is not None:
            try:
                self._chip.close()
            except Exception:  # noqa: BLE001
                pass
            self._chip = None

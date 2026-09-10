"""Shared test helpers for pympacds-gpio (REQ-GPIO-009)."""


class FakeEdgeEvent:
    def __init__(self, offset):
        self.line_offset = offset


class FakeChip:
    """A gpiod-like chip with an in-memory line model."""

    def __init__(self, path="/dev/gpiochip0", values=None, output_capable=None):
        self.path = path
        self._values = dict(values or {})
        self._output_capable = output_capable  # None = all capable
        self._active_low = set()
        self.requested = None
        self.released = False
        self.closed = False
        self._pending = []
        self.last_timeout = None

    def request_lines(
        self,
        input_offsets=None,
        watch_offsets=None,
        output_offsets=None,
        edge=None,
        debounce_us=None,
        active_low=None,
        defaults=None,
    ):
        self.requested = {
            "input": list(input_offsets or []),
            "watch": list(watch_offsets or []),
            "output": list(output_offsets or []),
            "edge": edge,
            "debounce_us": debounce_us,
            "active_low": active_low,
            "defaults": dict(defaults or {}),
        }
        if active_low:
            self._active_low = set(watch_offsets or [])
        for off in output_offsets or []:
            if self._output_capable is not None and not self._output_capable.get(off, True):
                return False
            self._values[off] = bool((defaults or {}).get(off, False))
        return True

    def get_value(self, offset):
        raw = self._values.get(offset, False)
        if offset in self._active_low:
            return not raw
        return raw

    def set_value(self, offset, value):
        self._values[offset] = bool(value)
        return True

    def push_event(self, offset, value):
        self._values[offset] = bool(value)
        self._pending.append(offset)

    def wait_edge_events(self, timeout=None):
        self.last_timeout = timeout
        pending, self._pending = self._pending, []
        return [FakeEdgeEvent(off) for off in pending]

    def release(self):
        self.released = True

    def close(self):
        self.closed = True


class FakeShim:
    """A GpioShim stand-in used for service-level and black-box tests."""

    def __init__(self, values=None):
        self._values = dict(values or {})
        self.opened = False
        self.released = False
        self.requested = None
        self._events = []

    def open(self):
        self.opened = True
        return True

    def request_lines(
        self, input_offsets, watch_offsets, output_offsets, edge, debounce_us, active_low, defaults
    ):
        self.requested = {
            "input": list(input_offsets),
            "watch": list(watch_offsets),
            "output": list(output_offsets),
            "edge": edge,
            "debounce_us": debounce_us,
            "active_low": active_low,
            "defaults": dict(defaults),
        }
        for off, val in (defaults or {}).items():
            self._values[off] = val
        return True

    def chip_path(self):
        return "/dev/gpiochip0"

    def get_value(self, offset):
        return self._values.get(offset, False)

    def set_value(self, offset, value):
        self._values[offset] = bool(value)
        return True

    def wait_events(self, timeout_s=0.05):
        events, self._events = self._events, []
        return events

    def release(self):
        self.released = True


def make_config(**overrides):
    defaults = {
        "chip": "",
        "lines": "",
        "watch_lines": "",
        "edge": "both",
        "debounce_us": "0",
        "active_low": "false",
        "output_lines": "",
        "default_values": "",
    }
    defaults.update(overrides)
    return defaults

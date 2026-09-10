"""Shim unit tests (REQ-GPIO-SHIM-*, REQ-GPIO-009)."""

import logging

import pytest
from helpers import FakeChip, make_config

from pympacds_gpio.shim import GpioShim

logger = logging.getLogger("test_shim")
logger.setLevel(logging.CRITICAL)


def _shim(chip_obj, **cfg):
    config = make_config(**cfg)
    return GpioShim(config, logger, chip_factory=lambda p: chip_obj)


def _real_shim(**cfg):
    """Return a shim on the real gpiod code path (no chip_factory)."""
    return GpioShim(make_config(**cfg), logger)


def _require_gpiod():
    import pympacds_gpio.shim as shim_mod

    if shim_mod.gpiod is None:
        pytest.skip("gpiod not installed")


def _opened(chip, input_offsets=(), watch_offsets=(), output_offsets=(), **cfg):
    """Return a shim that has opened the chip and requested the given lines."""
    shim = _shim(chip, **cfg)
    assert shim.open() is True
    assert (
        shim.request_lines(
            list(input_offsets),
            list(watch_offsets),
            list(output_offsets),
            cfg.get("edge", "both"),
            cfg.get("debounce_us", 0),
            cfg.get("active_low", False),
            cfg.get("defaults", {}),
        )
        is True
    )
    return shim


class TestOpen:
    def test_open_uses_factory_path(self):
        chip = FakeChip(path="/dev/gpiochip2")
        shim = _shim(chip, chip="/dev/gpiochip2")
        assert shim.open() is True
        assert shim.chip_path() == "/dev/gpiochip2"

    def test_open_empty_chip_falls_back_to_fake_path(self):
        chip = FakeChip(path="/dev/gpiochip0")
        shim = _shim(chip, chip="")
        assert shim.open() is True
        assert shim.chip_path() == "/dev/gpiochip0"

    def test_open_failure_returns_false(self):
        class BadChip:
            def __init__(self, path):
                raise OSError("no such device")

        shim = GpioShim(make_config(), logger, chip_factory=BadChip)
        assert shim.open() is False
        assert shim.chip_path() == ""

    def test_autodetect_chip_sorts_results(self, monkeypatch):
        monkeypatch.setattr(
            "glob.glob", lambda p: ["/dev/gpiochip3", "/dev/gpiochip1", "/dev/gpiochip0"]
        )
        assert GpioShim._autodetect_chip() == "/dev/gpiochip0"

    def test_autodetect_chip_no_matches(self, monkeypatch):
        monkeypatch.setattr("glob.glob", lambda p: [])
        assert GpioShim._autodetect_chip() == "/dev/gpiochip0"


class TestRequestLines:
    def test_builds_line_config(self):
        chip = FakeChip()
        _opened(
            chip,
            [1],
            [2],
            [3],
            edge="both",
            debounce_us=100,
            active_low=True,
            defaults={3: True},
        )
        assert chip.requested["input"] == [1]
        assert chip.requested["watch"] == [2]
        assert chip.requested["output"] == [3]
        assert chip.requested["edge"] == "both"
        assert chip.requested["debounce_us"] == 100
        assert chip.requested["active_low"] is True
        assert chip.requested["defaults"] == {3: True}

    def test_request_lines_fails_on_non_output_capable(self):
        chip = FakeChip(output_capable={5: False})
        shim = _shim(chip)
        shim.open()
        assert shim.request_lines([], [], [5], "both", 0, False, {}) is False

    def test_request_lines_applies_output_defaults(self):
        chip = FakeChip()
        shim = _opened(chip, output_offsets=[3], defaults={3: True})
        assert shim.get_value(3) is True

    def test_request_lines_empty_sets(self):
        chip = FakeChip()
        shim = _shim(chip)
        shim.open()
        assert shim.request_lines([], [], [], "both", 0, False, {}) is True
        assert chip.requested["input"] == []
        assert chip.requested["watch"] == []
        assert chip.requested["output"] == []

    def test_debounce_zero_not_recorded_as_active(self):
        chip = FakeChip()
        _opened(chip, watch_offsets=[0], debounce_us=0)
        assert chip.requested["debounce_us"] == 0


class TestValues:
    def test_get_value(self):
        chip = FakeChip(values={0: True})
        shim = _opened(chip, input_offsets=[0])
        assert shim.get_value(0) is True

    def test_get_value_unknown_offset_defaults_false(self):
        chip = FakeChip()
        shim = _opened(chip, input_offsets=[0])
        assert shim.get_value(99) is False

    def test_set_value(self):
        chip = FakeChip()
        shim = _opened(chip, output_offsets=[3])
        assert shim.set_value(3, True) is True
        assert chip._values[3] is True

    def test_set_value_oserror_returns_false(self):
        chip = FakeChip()
        shim = _opened(chip, output_offsets=[3])

        def bad_set(offset, value):
            raise OSError("write failed")

        chip.set_value = bad_set  # type: ignore[method-assign]
        assert shim.set_value(3, True) is False


class TestActiveLow:
    def test_active_low_inverts_watch_value(self):
        chip = FakeChip(values={0: False})
        shim = _opened(chip, watch_offsets=[0], active_low=True)
        assert shim.get_value(0) is True  # raw low, inverted

    def test_no_inversion_without_active_low(self):
        chip = FakeChip(values={0: False})
        shim = _opened(chip, watch_offsets=[0], active_low=False)
        assert shim.get_value(0) is False

    def test_active_low_does_not_invert_input_lines(self):
        chip = FakeChip(values={0: False, 1: False})
        shim = _opened(chip, input_offsets=[0], watch_offsets=[1], active_low=True)
        assert shim.get_value(0) is False  # input line untouched
        assert shim.get_value(1) is True  # watched line inverted


class TestEvents:
    def test_wait_events_returns_offset_value(self):
        chip = FakeChip()
        shim = _opened(chip, watch_offsets=[0])
        chip.push_event(0, True)
        assert shim.wait_events(0.1) == [(0, True)]

    def test_wait_events_drains(self):
        chip = FakeChip()
        shim = _opened(chip, watch_offsets=[0])
        chip.push_event(0, True)
        assert shim.wait_events(0.1) == [(0, True)]
        chip.push_event(0, False)
        assert shim.wait_events(0.1) == [(0, False)]
        assert shim.wait_events(0.1) == []

    def test_wait_events_empty(self):
        chip = FakeChip()
        shim = _opened(chip, watch_offsets=[0])
        assert shim.wait_events(0.1) == []


class TestRelease:
    def test_release_closes_chip_and_request(self):
        chip = FakeChip()
        shim = _opened(chip, input_offsets=[0])
        shim.release()
        assert chip.released is True
        assert chip.closed is True

    def test_release_without_request_closes_chip(self):
        chip = FakeChip()
        shim = _shim(chip)
        shim.open()
        shim.release()
        assert chip.closed is True

    def test_release_idempotent(self):
        chip = FakeChip()
        shim = _opened(chip, input_offsets=[0])
        shim.release()
        shim.release()  # must not raise


class TestBlockingPrevention:
    def test_wait_events_timeout_is_bounded(self):
        chip = FakeChip()
        shim = _opened(chip, watch_offsets=[0])
        shim.wait_events(timeout_s=0.05)
        assert chip.last_timeout <= 0.1


class TestCfgHelpers:
    def test_cfg_missing_section(self):
        shim = GpioShim(None, logger, chip_factory=lambda p: FakeChip())
        assert shim._cfg("chip", "default") == "default"

    def test_cfg_int_invalid(self):
        shim = GpioShim({"debounce_us": "abc"}, logger, chip_factory=lambda p: FakeChip())
        assert shim._cfg_int("debounce_us", 0) == 0

    def test_cfg_bool_true_variants(self):
        for v in ("true", "1", "yes", "on"):
            shim = GpioShim({"active_low": v}, logger, chip_factory=lambda p: FakeChip())
            assert shim._cfg_bool("active_low", False) is True

    def test_cfg_bool_false_variants(self):
        shim = GpioShim({"active_low": "no"}, logger, chip_factory=lambda p: FakeChip())
        assert shim._cfg_bool("active_low", True) is False


class TestRealGpiodPath:
    """Exercise the real gpiod code paths with a stubbed gpiod module."""

    def test_open_real_path(self, monkeypatch):
        _require_gpiod()
        calls = {}

        class FakeChip:
            def __init__(self, path):
                calls["path"] = path

        monkeypatch.setattr("gpiod.Chip", FakeChip)
        shim = _real_shim(chip="/dev/gpiochip0")
        assert shim.open() is True
        assert calls["path"] == "/dev/gpiochip0"
        assert shim.chip_path() == "/dev/gpiochip0"

    def test_open_real_path_autodetect(self, monkeypatch):
        _require_gpiod()
        calls = {}

        class FakeChip:
            def __init__(self, path):
                calls["path"] = path

        monkeypatch.setattr("gpiod.Chip", FakeChip)
        monkeypatch.setattr("glob.glob", lambda p: ["/dev/gpiochip1"])
        shim = _real_shim(chip="")
        assert shim.open() is True
        assert calls["path"] == "/dev/gpiochip1"

    def test_open_real_path_oserror(self, monkeypatch):
        _require_gpiod()

        class BadChip:
            def __init__(self, path):
                raise OSError("no device")

        monkeypatch.setattr("gpiod.Chip", BadChip)
        shim = _real_shim(chip="/dev/gpiochip0")
        assert shim.open() is False

    def test_open_gpiod_not_installed(self, monkeypatch):
        monkeypatch.setattr("pympacds_gpio.shim.gpiod", None)
        shim = _real_shim(chip="/dev/gpiochip0")
        assert shim.open() is False

    def test_request_lines_real_path(self, monkeypatch):
        _require_gpiod()
        settings = []

        class FakeLineSettings:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                settings.append(kwargs)

        class FakeRequest:
            pass

        def fake_request_lines(chip, config=None, consumer=None):
            return FakeRequest()

        monkeypatch.setattr("gpiod.LineSettings", FakeLineSettings)
        monkeypatch.setattr("gpiod.request_lines", fake_request_lines)

        shim = _real_shim()
        shim._chip = object()
        assert shim.request_lines([1], [2], [3], "both", 100, True, {3: True}) is True
        assert len(settings) == 3
        assert shim._lines is not None

    def test_request_lines_real_path_oserror(self, monkeypatch):
        _require_gpiod()

        class FakeLineSettings:
            def __init__(self, **kwargs):
                pass

        def fake_request_lines(chip, config=None, consumer=None):
            raise OSError("no lines")

        monkeypatch.setattr("gpiod.LineSettings", FakeLineSettings)
        monkeypatch.setattr("gpiod.request_lines", fake_request_lines)

        shim = _real_shim()
        shim._chip = object()
        assert shim.request_lines([], [], [], "both", 0, False, {}) is False


class TestReleaseExceptions:
    def test_release_swallows_release_and_close_errors(self):
        chip = FakeChip()
        shim = _opened(chip, input_offsets=[0])

        def bad_release():
            raise RuntimeError("boom")

        def bad_close():
            raise RuntimeError("boom")

        chip.release = bad_release  # type: ignore[method-assign]
        chip.close = bad_close  # type: ignore[method-assign]
        shim.release()  # must not raise

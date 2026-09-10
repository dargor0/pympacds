"""[gpio] schema validation tests (REQ-SVC-008)."""

import configparser
from pathlib import Path

from pympacds.config import ConfigManager

SCHEMA = Path(__file__).parent.parent / "config" / "gpio.schema.json"


def _validate(gpio_kv):
    cp = configparser.ConfigParser()
    cp["gpio"] = gpio_kv
    return ConfigManager(schema_file=str(SCHEMA)).validate(cp)


def test_valid_config_passes():
    assert _validate({"chip": "", "edge": "both", "debounce_us": "0"}) == []


def test_invalid_edge_rejected():
    assert any("edge" in e for e in _validate({"edge": "sideways"}))


def test_negative_debounce_rejected():
    assert any("debounce_us" in e for e in _validate({"debounce_us": "-1"}))


def test_invalid_bool_rejected():
    assert any("active_low" in e for e in _validate({"active_low": "maybe"}))

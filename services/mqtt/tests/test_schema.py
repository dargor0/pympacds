"""[mqtt] schema validation tests (REQ-SVC-008)."""

import configparser
from pathlib import Path

from pympacds.config import ConfigManager

SCHEMA = Path(__file__).parent.parent / "config" / "mqtt.schema.json"


def _validate(mqtt_kv):
    cp = configparser.ConfigParser()
    cp["mqtt"] = mqtt_kv
    return ConfigManager(schema_file=str(SCHEMA)).validate(cp)


def test_valid_config_passes():
    assert _validate({"host": "localhost", "port": "1883", "qos": "1"}) == []


def test_invalid_port_rejected():
    assert any("port" in e for e in _validate({"port": "not-a-number"}))


def test_port_out_of_range_rejected():
    assert any("port" in e for e in _validate({"port": "70000"}))


def test_invalid_qos_rejected():
    assert any("qos" in e for e in _validate({"qos": "5"}))


def test_invalid_mqtt_version_rejected():
    assert any("mqtt_version" in e for e in _validate({"mqtt_version": "3.2"}))


def test_invalid_bool_rejected():
    assert any("use_tls" in e for e in _validate({"use_tls": "maybe"}))

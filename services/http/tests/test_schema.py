"""[http] schema validation tests (REQ-SVC-008)."""

import configparser
from pathlib import Path

from pympacds.config import ConfigManager

SCHEMA = Path(__file__).parent.parent / "config" / "http.schema.json"


def _validate(http_kv):
    cp = configparser.ConfigParser()
    cp["http"] = http_kv
    return ConfigManager(schema_file=str(SCHEMA)).validate(cp)


def test_valid_config_passes():
    assert _validate({"timeout_s": "10", "retries": "3", "default_method": "POST"}) == []


def test_invalid_int_rejected():
    assert any("timeout_s" in e for e in _validate({"timeout_s": "not-a-number"}))


def test_negative_retries_rejected():
    assert any("retries" in e for e in _validate({"retries": "-1"}))


def test_invalid_bool_rejected():
    assert any("tls_verify" in e for e in _validate({"tls_verify": "maybe"}))


def test_invalid_method_rejected():
    assert any("default_method" in e for e in _validate({"default_method": "BANANA"}))


def test_max_redirects_out_of_range():
    assert any("max_redirects" in e for e in _validate({"max_redirects": "-2"}))

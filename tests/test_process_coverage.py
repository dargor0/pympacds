"""Additional coverage tests for ProcessBase (validation, tags, middleware)."""

import asyncio
import configparser
import json
import logging
import pytest

from pympacds.process import ProcessBase


def _write(ini_file, updates):
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    for section, kv in updates.items():
        if section != "DEFAULT" and not cp.has_section(section):
            cp.add_section(section)
        for k, v in kv.items():
            cp[section][k] = v
    with open(ini_file, "w") as f:
        cp.write(f)


# ------------------------------------------------------------------
# built-in validation error branches
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "section,key,val",
    [
        ("DEFAULT", "logstdout", "maybe"),
        ("DEFAULT", "logmaxsize", "0"),
        ("DEFAULT", "logmaxcount", "-1"),
        ("dbus", "drain_timeout_ms", "-1"),
        ("dbus", "contract_health", "maybe"),
        ("dbus", "heartbeat_interval_s", "1"),
        ("dbus", "heartbeat_interval_s", "4000"),
        ("dbus", "discovery_enabled", "maybe"),
    ],
)
def test_builtin_validation_errors(ini_file, section, key, val):
    _write(ini_file, {section: {key: val}})
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


# ------------------------------------------------------------------
# user schema validation branches
# ------------------------------------------------------------------


def _schema_setup(ini_file, tmp_path, schema, section_values):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema))
    updates = {"dbus": {"schema_file": str(schema_path)}}
    for section, kv in section_values.items():
        updates[section] = kv
    _write(ini_file, updates)


def test_schema_user_int_parse_error(ini_file, tmp_path):
    _schema_setup(ini_file, tmp_path, {"s": {"keys": {"n": {"type": "int"}}}}, {"s": {"n": "abc"}})
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_schema_user_int_min(ini_file, tmp_path):
    _schema_setup(ini_file, tmp_path, {"s": {"keys": {"n": {"type": "int", "min": 10}}}}, {"s": {"n": "5"}})
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_schema_user_float_max(ini_file, tmp_path):
    _schema_setup(ini_file, tmp_path, {"s": {"keys": {"n": {"type": "float", "max": 1.0}}}}, {"s": {"n": "2.5"}})
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_schema_user_pattern_match_ok(ini_file, tmp_path):
    _schema_setup(
        ini_file,
        tmp_path,
        {"s": {"keys": {"c": {"type": "str", "pattern": "^[A-Z]+$"}}}},
        {"s": {"c": "ABC"}},
    )
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is True


def test_schema_section_dbus_and_default_skipped(ini_file, tmp_path):
    _schema_setup(
        ini_file,
        tmp_path,
        {"dbus": {"required": ["nope"]}, "DEFAULT": {"required": ["nope"]}},
        {},
    )
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is True


def test_schema_section_absent_from_config_skipped(ini_file, tmp_path):
    _schema_setup(ini_file, tmp_path, {"absent": {"required": ["name"]}}, {})
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is True


# ------------------------------------------------------------------
# capability tags (REQ-SVC-014)
# ------------------------------------------------------------------


class _FakeTaggedIface:
    iface_provides = ("sensor", "temp")
    iface_requires = ("power",)


class _FakeTaggedBus:
    ifacelist = {"path": [_FakeTaggedIface()]}


def test_dbus_health_provides_requires(ini_file):
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p.bus = _FakeTaggedBus()
    assert p.dbus_health_get_provides() == ["sensor", "temp"]
    assert p.dbus_health_get_requires() == ["power"]


def test_dbus_health_no_bus(ini_file):
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    assert p.dbus_health_get_provides() == []
    assert p.dbus_health_get_requires() == []


# ------------------------------------------------------------------
# setup_dbus
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_dbus_creates_manager(ini_file, fake_dbus_backend):
    from pympacds.dbus import DBusManager

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p.setup_dbus()
    assert isinstance(p.bus, DBusManager)


# ------------------------------------------------------------------
# logging fallback branch
# ------------------------------------------------------------------


def test_setup_logging_invalid_loglevel_fallback(ini_file):
    _write(ini_file, {"DEFAULT": {"loglevel": "NOT_A_REAL_LEVEL"}})
    p = ProcessBase("t", "1.0")
    p.config.read(ini_file)
    p._setup_logging()
    assert p.logger.level == logging.WARNING


# ------------------------------------------------------------------
# middleware error paths
# ------------------------------------------------------------------


def test_resolve_middleware_string_not_found(ini_file):
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    assert p._resolve_middleware_class({}, "missing_mw") is None


def test_resolve_middleware_load_exception(ini_file):
    class BadEP:
        name = "bad"

        def load(self):
            raise RuntimeError("load failed")

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    assert p._resolve_middleware_class({"bad": BadEP()}, "bad") is None


def test_programmatic_middleware_instantiate_exception(ini_file):
    from pympacds.middleware import MiddlewareSpec

    class ExplodeMW:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p.register_middleware = lambda: [MiddlewareSpec(ExplodeMW, None)]
    p._init_middleware()
    assert p._middleware_instances == []


def test_programmatic_middleware_duplicate_skip(ini_file):
    from pympacds.middleware import MiddlewareBase, MiddlewareSpec

    class DupMW(MiddlewareBase):
        pass

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p.register_middleware = lambda: [MiddlewareSpec(DupMW, None), MiddlewareSpec(DupMW, None)]
    p._init_middleware()
    assert len(p._middleware_instances) == 1


def test_config_middleware_load_exception(ini_file, monkeypatch):
    import importlib.metadata

    class BadEP:
        name = "bad"

        def load(self):
            raise RuntimeError("load boom")

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [BadEP()])
    _write(ini_file, {"middleware": {"bad": "sec"}})

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p._init_middleware()
    assert p._middleware_instances == []


def test_config_middleware_instantiate_exception(ini_file, monkeypatch):
    import importlib.metadata

    class ExplodeMW:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    class FakeEP:
        name = "explode"

        def load(self):
            return ExplodeMW

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [FakeEP()])
    _write(ini_file, {"middleware": {"explode": "sec"}})

    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p._init_middleware()
    assert p._middleware_instances == []


# ------------------------------------------------------------------
# signal handler registration
# ------------------------------------------------------------------


def test_sighdl_register_without_running_loop(ini_file):
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p._sighdl_register_term()  # no running loop -> RuntimeError -> return


def test_sighdl_register_handler_error(ini_file, monkeypatch):
    class FakeLoop:
        def add_signal_handler(self, signum, callback):
            raise ValueError("no signals")

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    p._sighdl_register_term()

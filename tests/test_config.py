"""Tests for ConfigManager and schema validation (REQ-CONF-001 through REQ-CONF-008)."""

import configparser
import json
import os
import tempfile


class TestConfigManager:
    def test_construction(self):
        from pympacds.config import ConfigManager

        cm = ConfigManager()
        assert cm is not None

    def test_read_ini(self):
        from pympacds.config import ConfigManager

        cm = ConfigManager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[DEFAULT]\nkey = value\n")
            path = f.name

        cp = cm.read_ini(path)
        assert cp["DEFAULT"]["key"] == "value"
        os.unlink(path)

    def test_write_ini(self):
        from pympacds.config import ConfigManager

        cm = ConfigManager()
        cp = configparser.ConfigParser()
        cp["test"] = {"answer": "42"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            path = f.name

        cm.write_ini(cp, path)
        cp2 = configparser.ConfigParser()
        cp2.read(path)
        assert cp2["test"]["answer"] == "42"
        os.unlink(path)

    def test_validate_no_schema(self):
        from pympacds.config import ConfigManager

        cm = ConfigManager()
        cp = configparser.ConfigParser()
        errors = cm.validate(cp)
        assert errors == []

    def test_validate_with_schema(self):
        from pympacds.config import ConfigManager

        schema = {
            "myservice": {
                "required": ["name"],
                "keys": {
                    "name": {"type": "str", "pattern": "^[a-z]+$"},
                    "count": {"type": "int", "min": 1, "max": 100},
                },
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name

        cm = ConfigManager(schema_file=schema_path)
        cp = configparser.ConfigParser()
        cp["myservice"] = {"name": "hello", "count": "5"}
        errors = cm.validate(cp)
        assert errors == []
        os.unlink(schema_path)

    def test_validate_missing_required(self):
        from pympacds.config import ConfigManager

        schema = {
            "myservice": {
                "required": ["name"],
                "keys": {"name": {"type": "str"}},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name

        cm = ConfigManager(schema_file=schema_path)
        cp = configparser.ConfigParser()
        cp["myservice"] = {}
        errors = cm.validate(cp)
        assert any("required" in e.lower() for e in errors)
        os.unlink(schema_path)

    def test_validate_type_error(self):
        from pympacds.config import ConfigManager

        schema = {
            "myservice": {
                "keys": {"count": {"type": "int"}},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name

        cm = ConfigManager(schema_file=schema_path)
        cp = configparser.ConfigParser()
        cp["myservice"] = {"count": "notanumber"}
        errors = cm.validate(cp)
        assert any("int" in e for e in errors)
        os.unlink(schema_path)

    def test_validate_range_error(self):
        from pympacds.config import ConfigManager

        schema = {
            "myservice": {
                "keys": {"count": {"type": "int", "min": 1, "max": 10}},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name

        cm = ConfigManager(schema_file=schema_path)
        cp = configparser.ConfigParser()
        cp["myservice"] = {"count": "50"}
        errors = cm.validate(cp)
        assert any("max" in e for e in errors)
        os.unlink(schema_path)


"""Additional tests for config validation edge cases."""


class TestConfigValidationEdgeCases:
    def test_validate_bool_type(self):
        from pympacds.config import ConfigManager

        schema = {"s": {"keys": {"flag": {"type": "bool"}}}}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(schema, f)
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        cp["s"] = {"flag": "true"}
        assert cm.validate(cp) == []

        cp["s"] = {"flag": "bad"}
        errors = cm.validate(cp)
        assert any("bool" in e for e in errors)
        os.unlink(path)

    def test_validate_float_type(self):
        from pympacds.config import ConfigManager

        schema = {"s": {"keys": {"rate": {"type": "float", "min": 0.0, "max": 1.0}}}}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(schema, f)
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        cp["s"] = {"rate": "0.5"}
        assert cm.validate(cp) == []

        cp["s"] = {"rate": "1.5"}
        errors = cm.validate(cp)
        assert any("max" in e for e in errors)

        cp["s"] = {"rate": "notfloat"}
        errors = cm.validate(cp)
        assert any("float" in e for e in errors)
        os.unlink(path)

    def test_validate_pattern(self):
        from pympacds.config import ConfigManager

        schema = {"s": {"keys": {"code": {"type": "str", "pattern": "^[A-Z]{3}$"}}}}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(schema, f)
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        cp["s"] = {"code": "ABC"}
        assert cm.validate(cp) == []

        cp["s"] = {"code": "abc"}
        errors = cm.validate(cp)
        assert any("pattern" in e for e in errors)
        os.unlink(path)

    def test_validate_default_applied(self):
        from pympacds.config import ConfigManager

        schema = {"s": {"keys": {"opt": {"type": "str", "default": "hello"}}}}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(schema, f)
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        cp["s"] = {}
        errors = cm.validate(cp)
        assert errors == []
        assert cp["s"]["opt"] == "hello"
        os.unlink(path)

    def test_validate_bad_schema_file(self):
        from pympacds.config import ConfigManager

        cm = ConfigManager(schema_file="/nonexistent/schema.json")
        cp = configparser.ConfigParser()
        errors = cm.validate(cp)
        assert any("Cannot load" in e for e in errors)

    def test_validate_bad_json(self):
        from pympacds.config import ConfigManager

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("not json")
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        errors = cm.validate(cp)
        assert any("Cannot load" in e for e in errors)
        os.unlink(path)

    def test_validate_skips_default_and_dbus(self):
        from pympacds.config import ConfigManager

        schema = {
            "DEFAULT": {"keys": {}},
            "dbus": {"keys": {}},
            "myservice": {"keys": {"name": {"type": "str"}}},
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(schema, f)
            path = f.name

        cm = ConfigManager(schema_file=path)
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"anything": "x"}
        cp["dbus"] = {"anything": "y"}
        cp["myservice"] = {"name": "test"}
        errors = cm.validate(cp)
        assert errors == []
        os.unlink(path)

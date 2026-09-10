"""Final push to fix process.py coverage — target specific missed lines."""

import asyncio
import configparser
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class MockBus:
    async def connect(self):
        return self

    async def request_name(self, *a):
        pass

    async def stop(self):
        pass

    def disconnect(self):
        pass

    async def wait_for_disconnect(self):
        pass

    @property
    def _writer(self):
        class W:
            class M:
                def __len__(self):
                    return 0

            messages = M()

        return W()


@pytest.mark.asyncio
async def test_full_lifecycle_with_tasks(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("x", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

        def update_tasks(self):
            if "e" not in self.tasklist:

                async def done():
                    self.exitevent.set()

                self.tasklist["e"] = asyncio.create_task(done(), name="e")

    svc = Svc()
    svc.setup(["-c", ini_file])
    await asyncio.wait_for(svc.main_loop(), timeout=2)


@pytest.mark.asyncio
async def test_close_loop_with_bus_stop(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("y", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert await svc.init_loop()
    svc.exitevent.set()
    await svc.close_loop()


@pytest.mark.asyncio
async def test_middleware_init_discovery(ini_file, monkeypatch):
    import importlib.metadata

    from pympacds.process import ProcessBase

    class FakeEP:
        name = "test_mw"

        def load(self):
            from pympacds.middleware import MiddlewareBase

            class TMW(MiddlewareBase):
                pass

            return TMW

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [FakeEP()])

    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["middleware"] = {"test_mw": "my_section"}
    cp["my_section"] = {"k": "v"}
    with open(ini_file, "w") as f:
        cp.write(f)

    p = ProcessBase("z", "1.0")
    p.setup(["-c", ini_file])
    p._init_middleware()
    assert len(p._middleware_instances) == 1


def test_required_key_missing_schema(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"required": ["name"], "keys": {"name": {"type": "str"}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {}  # no "name" key
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("a", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_schema_pattern_match(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"c": {"type": "str", "pattern": "^[A-Z]+$"}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {"c": "abc"}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("b", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_no_dbus_section(ini_file):
    from pympacds.process import ProcessBase

    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("c", "1.0")
    assert p.setup(["-c", ini_file]) is True


def test_setup_returns_false_on_validate_fail(ini_file):
    from pympacds.process import ProcessBase

    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["DEFAULT"]["loglevel"] = "INVALID"
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("d", "1.0")
    assert p.setup(["-c", ini_file]) is False

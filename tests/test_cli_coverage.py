"""Extra branch coverage for pympacds.cli (list/setup/RT warning paths)."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pympacds import cli  # noqa: E402


def write_ini(tmp_path, body: str) -> str:
    p = tmp_path / "svc.ini"
    p.write_text(body)
    return str(p)


def test_install_realtime_warning_only(tmp_path, capsys):
    config = write_ini(
        tmp_path,
        "[rt]\nsched_policy = fifo\nsched_priority = 10\nnice = -5\n",
    )
    args = SimpleNamespace(config=config, dry_run=True)
    assert cli._install_realtime("test_svc", args, dry_run=True) is False
    assert "warning: [rt]" in capsys.readouterr().err


def test_cmd_setup_creates_user(monkeypatch):
    calls = []

    def fake_run(cmd, check=True, quiet=False):
        calls.append(cmd)
        rc = 1 if cmd[:1] == ["id"] else 0
        return type("R", (), {"returncode": rc})()

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    monkeypatch.setattr("pympacds.cli._run", fake_run)
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    class Args:
        resource_dir = "/x"
        bus_prefix = "t"
        no_dirs = False
        dry_run = False
        quiet = False

    cli._cmd_setup(Args)
    assert any("adduser" in " ".join(c) for c in calls)
    assert any("chown" in " ".join(c) for c in calls)


class _FakeIntrospect:
    def __init__(self, *a, **k):
        pass

    def find_property(self, name):
        return [("org.pympacds.Health", name)]


def test_cmd_list_with_matching_service(monkeypatch, capsys):
    class FakeMessage:
        def __init__(self, **kw):
            self.kw = kw

    class FakeBusType:
        SYSTEM = 1
        SESSION = 2

    class FakeLib:
        Message = FakeMessage
        BusType = FakeBusType

    class FakeBus:
        def __init__(self, **kw):
            pass

        async def connect(self):
            return self

        def disconnect(self):
            pass

        async def wait_for_disconnect(self):
            return None

        async def call(self, msg):
            member = msg.kw.get("member")
            if member == "ListNames":
                return type("R", (), {"body": [["org.pympacds.demo"]]})()
            if member == "GetConnectionUnixProcessID":
                return type("R", (), {"body": [123]})()
            value = type("V", (), {"value": ["health"]})()
            return type("R", (), {"body": [value]})()

        async def introspect(self, name, path):
            return type("N", (), {"tostring": lambda self: "<node/>"})()

    class FakeAio:
        MessageBus = FakeBus

    monkeypatch.setattr("pympacds.get_dbus_aio", lambda: FakeAio())
    monkeypatch.setattr("pympacds.get_dbus_lib", lambda: FakeLib())
    monkeypatch.setattr("pympacds.introspect.BusIntrospect", _FakeIntrospect)

    cli._cmd_list(SimpleNamespace())
    assert "org.pympacds.demo" in capsys.readouterr().out

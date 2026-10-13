"""Unit tests for the black-box harness knobs (REQ-XCUT-002.1).

Covers the environment-driven parameterization used by the reusable D-Bus
suite: ``MPACDS_CMD_<NAME>`` (launch-command override) and the ``launch_service``
fixture's argv/env handling. Only stub ``python -c`` commands are spawned, so
these tests need no D-Bus daemon and no service binary.

``service_command`` / ``BUS_PREFIX`` are defined in ``tests/conftest.py``.
"""

import shlex
import sys

from conftest import service_command

_PRINT = (
    "import os, sys;"
    "print('ARGV=' + repr(sys.argv[1:]));"
    "print('ADDR=' + str(os.environ.get('DBUS_SYSTEM_BUS_ADDRESS')));"
    "print('PP=' + str(os.environ.get('PYTHONPATH')))"
)

_ADDR = "unix:path=/tmp/fakebus"


def test_service_command_default(monkeypatch):
    monkeypatch.delenv("MPACDS_CMD_FOO", raising=False)
    assert service_command("foo", ["python", "foo.py"]) == ["python", "foo.py"]


def test_service_command_override(monkeypatch):
    monkeypatch.setenv("MPACDS_CMD_FOO", "/usr/bin/cppmpacds-foo --flag")
    assert service_command("foo", ["python", "foo.py"]) == ["/usr/bin/cppmpacds-foo", "--flag"]


def test_service_command_name_is_upper(monkeypatch):
    monkeypatch.setenv("MPACDS_CMD_MQTT", "/usr/bin/cppmpacds-mqtt")
    result = service_command("mqtt", ["python", "-m", "pympacds_mqtt.service"])
    assert result == ["/usr/bin/cppmpacds-mqtt"]


def test_launch_service_appends_config_and_env(tmp_path, launch_service, monkeypatch):
    monkeypatch.delenv("MPACDS_CMD_FOO", raising=False)
    ini = tmp_path / "foo.ini"
    ini.write_text("")

    proc = launch_service("foo", ini, _ADDR, [sys.executable, "-c", _PRINT])
    out, _ = proc.communicate(timeout=15)

    assert "ARGV=['-c', " in out
    assert str(ini) in out
    assert f"ADDR={_ADDR}" in out


def test_launch_service_passes_extra_env(tmp_path, launch_service, monkeypatch):
    monkeypatch.delenv("MPACDS_CMD_FOO", raising=False)
    ini = tmp_path / "foo.ini"
    ini.write_text("")

    proc = launch_service(
        "foo",
        ini,
        _ADDR,
        [sys.executable, "-c", _PRINT],
        extra_env={"PYTHONPATH": "/some/path"},
    )
    out, _ = proc.communicate(timeout=15)
    assert "PP=/some/path" in out


def test_launch_service_uses_override(tmp_path, launch_service, monkeypatch):
    override_code = "import sys;print('OVERRIDE=' + repr(sys.argv[1:]))"
    monkeypatch.setenv("MPACDS_CMD_FOO", shlex.join([sys.executable, "-c", override_code]))
    ini = tmp_path / "foo.ini"
    ini.write_text("")

    # the default command exits non-zero; the override prints the marker instead
    proc = launch_service(
        "foo",
        ini,
        _ADDR,
        [sys.executable, "-c", "raise SystemExit(3)"],
    )
    out, _ = proc.communicate(timeout=15)
    assert "OVERRIDE=['-c', " in out
    assert proc.returncode == 0

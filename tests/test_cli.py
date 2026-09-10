"""Integration tests for the CLI tool."""

import subprocess
import sys
import os

_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _run_cli(*args):
    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = _PKG_DIR + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = _PKG_DIR
    return subprocess.run(
        [sys.executable, "-m", "pympacds.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_help():
    r = _run_cli("--help")
    assert r.returncode == 0, r.stderr


def test_cli_config_show_help():
    r = _run_cli("config", "show", "--help")
    assert r.returncode == 0


def test_cli_setup_help():
    r = _run_cli("setup", "--help")
    assert r.returncode == 0


def test_cli_new_help():
    r = _run_cli("new", "--help")
    assert r.returncode == 0


def test_cli_install_help():
    r = _run_cli("install", "--help")
    assert r.returncode == 0


def test_cli_config_help():
    r = _run_cli("config", "--help")
    assert r.returncode == 0


def test_cli_list():
    r = _run_cli("list")
    assert r.returncode == 0


"""Direct unit tests for CLI helper functions."""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestCLIHelpers:
    def test_install_template(self, tmp_path):
        from pympacds.cli import _install_template

        src = tmp_path / "template.in"
        dst = tmp_path / "output.conf"
        src.write_text("prefix=@BUS_PREFIX@\nown=@BUS_PREFIX@")

        _install_template(str(src), str(dst), "com.example", dry_run=False)
        content = dst.read_text()
        assert "com.example" in content
        assert "@BUS_PREFIX@" not in content

    def test_install_template_dry_run(self, tmp_path):
        from pympacds.cli import _install_template

        src = tmp_path / "template.in"
        dst = tmp_path / "output.conf"
        src.write_text("prefix=@BUS_PREFIX@")

        _install_template(str(src), str(dst), "com.example", dry_run=True)
        assert not dst.exists()

    def test_update_file_prefix(self, tmp_path):
        from pympacds.cli import _update_file_prefix

        dst = tmp_path / "policy.conf"
        dst.write_text('<allow own_prefix="org.old"/>')

        _update_file_prefix(str(dst), "com.new", dry_run=False)
        content = dst.read_text()
        assert "com.new" in content
        assert "org.old" not in content

    def test_update_file_prefix_dry_run(self, tmp_path):
        from pympacds.cli import _update_file_prefix

        dst = tmp_path / "policy.conf"
        dst.write_text('<allow own_prefix="org.old"/>')

        _update_file_prefix(str(dst), "com.new", dry_run=True)
        assert "org.old" in dst.read_text()

    def test_update_file_prefix_same(self, tmp_path):
        from pympacds.cli import _update_file_prefix

        dst = tmp_path / "policy.conf"
        dst.write_text('<allow own="org.pympacds"/>')

        _update_file_prefix(str(dst), "org.pympacds", dry_run=False)
        # unchanged
        assert "org.pympacds" in dst.read_text()

    def test_find_resource_default(self):
        from pympacds.cli import _find_resource

        result = _find_resource("nonexistent_file.xyz")
        assert "nonexistent_file.xyz" in result

    def test_run_success(self):
        from pympacds.cli import _run

        r = _run(["echo", "hello"], check=False, quiet=True)
        assert r.returncode == 0

    def test_run_file_not_found(self):
        from pympacds.cli import _run

        r = _run(["/nonexistent/binary_xyz_123"], check=False, quiet=True)
        assert r.returncode != 0

    def test_ensure_root(self, monkeypatch):
        from pympacds.cli import _ensure_root

        monkeypatch.setattr(os, "geteuid", lambda: 0)
        _ensure_root()

    def test_ensure_root_fails(self, monkeypatch):
        from pympacds.cli import _ensure_root

        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        with pytest.raises(SystemExit):
            _ensure_root()

    def test_validate_builtin_ok(self):
        import configparser
        from pympacds.cli import _validate_builtin

        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        cp["dbus"] = {"bus_type": "system"}
        assert _validate_builtin(cp) == []

    def test_validate_builtin_invalid_loglevel(self):
        import configparser
        from pympacds.cli import _validate_builtin

        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "INVALID", "logstdout": "false", "logfile": ""}
        errors = _validate_builtin(cp)
        assert any("loglevel" in e for e in errors)

    def test_validate_builtin_invalid_bus_type(self):
        import configparser
        from pympacds.cli import _validate_builtin

        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        cp["dbus"] = {"bus_type": "bad"}
        errors = _validate_builtin(cp)
        assert any("bus_type" in e for e in errors)

    def test_validate_builtin_no_dbus_section(self):
        import configparser
        from pympacds.cli import _validate_builtin

        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        assert _validate_builtin(cp) == []

"""CLI integration tests — subprocess for help, direct import for logic."""

import configparser
import os
import subprocess
import sys

import pytest

_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _run_cli(*args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
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


# ------------------------------------------------------------------
# subprocess-based (help, list, setup dry-run)
# ------------------------------------------------------------------


class TestCLIHelp:
    def test_help(self):
        r = _run_cli("--help")
        assert r.returncode == 0

    def test_setup_help(self):
        r = _run_cli("setup", "--help")
        assert r.returncode == 0

    def test_install_help(self):
        r = _run_cli("install", "--help")
        assert r.returncode == 0

    def test_new_help(self):
        r = _run_cli("new", "--help")
        assert r.returncode == 0

    def test_config_help(self):
        r = _run_cli("config", "--help")
        assert r.returncode == 0

    def test_config_show_help(self):
        r = _run_cli("config", "show", "--help")
        assert r.returncode == 0


class TestCLISetup:
    def test_dry_run(self):
        r = _run_cli("setup", "--dry-run")
        assert r.returncode == 0

    def test_dry_run_with_prefix(self):
        r = _run_cli("setup", "--dry-run", "--bus-prefix", "com.example.test")
        assert r.returncode == 0


class TestCLIList:
    def test_list_with_bus(self, session_bus_address):
        r = _run_cli(
            "list",
            env_extra={"DBUS_SYSTEM_BUS_ADDRESS": session_bus_address},
        )
        assert r.returncode == 0

    def test_list_direct(self, session_bus_address, monkeypatch):
        """Direct coverage of _cmd_list."""
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "list"])
        try:
            from pympacds.cli import main

            main()
        finally:
            os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)


# ------------------------------------------------------------------
# direct import (new, new-middleware, config commands)
# ------------------------------------------------------------------


class TestCLINew:
    def test_new_scaffolding(self, tmp_path, monkeypatch):
        cwd = os.getcwd()
        os.chdir(str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "new", "my_service"])
        try:
            from pympacds.cli import main

            main()
            assert os.path.isdir("my_service/src/my_service")
            assert os.path.isfile("my_service/pyproject.toml")
            assert os.path.isfile("my_service/src/my_service/main.py")
            assert os.path.isfile("my_service/config/my_service.ini")
            assert os.path.isfile("my_service/tests/test_main.py")
        finally:
            os.chdir(cwd)

    def test_new_generates_valid_python(self, tmp_path, monkeypatch):
        cwd = os.getcwd()
        os.chdir(str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "new", "svc"])
        try:
            from pympacds.cli import main

            main()
            with open("svc/src/svc/main.py") as f:
                code = f.read()
            compile(code, "main.py", "exec")
        finally:
            os.chdir(cwd)

    def test_new_middleware_scaffolding(self, tmp_path, monkeypatch):
        cwd = os.getcwd()
        os.chdir(str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "new-middleware", "my_mw"])
        try:
            from pympacds.cli import main

            main()
            assert os.path.isdir("my_mw/src/pympacds_middleware_my_mw")
            assert os.path.isfile("my_mw/pyproject.toml")
            assert os.path.isfile("my_mw/src/pympacds_middleware_my_mw/middleware.py")
        finally:
            os.chdir(cwd)

    def test_new_middleware_generates_valid_python(self, tmp_path, monkeypatch):
        cwd = os.getcwd()
        os.chdir(str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "new-middleware", "mw"])
        try:
            from pympacds.cli import main

            main()
            with open("mw/src/pympacds_middleware_mw/middleware.py") as f:
                code = f.read()
            compile(code, "middleware.py", "exec")
        finally:
            os.chdir(cwd)


class TestCLIConfig:
    def test_show(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        cp["myservice"] = {"name": "hello"}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "show", "-c", str(cfg)])
        stdout_buf = []

        def capture(*a, **kw):
            stdout_buf.append(" ".join(str(x) for x in a))

        monkeypatch.setattr("builtins.print", capture)
        from pympacds.cli import main

        main()
        assert any("name" in s for s in stdout_buf)

    def test_get(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["myservice"] = {"name": "hello"}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pympacds-admin", "config", "get", "-c", str(cfg), "myservice", "name"],
        )
        stdout_buf = []

        def capture(*a, **kw):
            stdout_buf.append(" ".join(str(x) for x in a))

        monkeypatch.setattr("builtins.print", capture)
        from pympacds.cli import main

        main()
        assert any("hello" in s for s in stdout_buf)

    def test_get_missing_key(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["myservice"] = {}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pympacds-admin", "config", "get", "-c", str(cfg), "myservice", "missing"],
        )
        from pympacds.cli import main

        with pytest.raises(SystemExit):
            main()

    def test_set(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["myservice"] = {"name": "old"}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pympacds-admin",
                "config",
                "set",
                "-c",
                str(cfg),
                "myservice",
                "name",
                "new",
            ],
        )
        from pympacds.cli import main

        main()
        cp2 = configparser.ConfigParser()
        cp2.read(cfg)
        assert cp2["myservice"]["name"] == "new"

    def test_set_new_section(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        monkeypatch.setattr(
            sys,
            "argv",
            ["pympacds-admin", "config", "set", "-c", str(cfg), "newsect", "k", "v"],
        )
        from pympacds.cli import main

        main()
        cp = configparser.ConfigParser()
        cp.read(cfg)
        assert cp["newsect"]["k"] == "v"

    def test_remove(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["myservice"] = {"name": "hello", "age": "42"}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pympacds-admin", "config", "remove", "-c", str(cfg), "myservice", "age"],
        )
        from pympacds.cli import main

        main()
        cp2 = configparser.ConfigParser()
        cp2.read(cfg)
        assert "age" not in cp2["myservice"]

    def test_validate_ok(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "validate", "-c", str(cfg)])
        from pympacds.cli import main

        main()

    def test_validate_fails(self, tmp_path, monkeypatch):
        cfg = tmp_path / "test.ini"
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "INVALID", "logstdout": "false", "logfile": ""}
        with open(cfg, "w") as f:
            cp.write(f)
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "validate", "-c", str(cfg)])
        from pympacds.cli import main

        with pytest.raises(SystemExit):
            main()

    def test_set_missing_config_file(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "set", "s", "k", "v"])
        from pympacds.cli import main

        with pytest.raises(SystemExit):
            main()

    def test_remove_missing_config_file(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "remove", "s", "k"])
        from pympacds.cli import main

        with pytest.raises(SystemExit):
            main()

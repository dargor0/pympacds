"""Recover coverage from accidentally deleted test files."""

import asyncio
import configparser
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ------------------------------------------------------------------
# process.py — lifecycle, middleware, validation
# ------------------------------------------------------------------


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
async def test_init_loop_success(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("a", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert await svc.init_loop()
    svc.exitevent.set()
    await svc.close_loop()


@pytest.mark.asyncio
async def test_init_loop_failure(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("b", "1.0")

        async def start_dbus(self):
            return False

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert not await svc.init_loop()


@pytest.mark.asyncio
async def test_close_loop_with_pending(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("c", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert await svc.init_loop()
    svc.tasklist["p"] = asyncio.create_task(asyncio.sleep(0.01), name="p")
    await asyncio.sleep(0.02)
    svc.exitevent.set()
    await svc.close_loop()


@pytest.mark.asyncio
async def test_main_loop_runs(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("d", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

        def update_tasks(self):
            if "e" not in self.tasklist:

                async def fire():
                    await asyncio.sleep(0.01)
                    self.exitevent.set()

                self.tasklist["e"] = asyncio.create_task(fire(), name="e")

    svc = Svc()
    svc.setup(["-c", ini_file])
    await asyncio.wait_for(svc.main_loop(), timeout=2)


@pytest.mark.asyncio
async def test_main_loop_init_fails(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("e", "1.0")

        async def start_dbus(self):
            return False

    svc = Svc()
    svc.setup(["-c", ini_file])
    await asyncio.wait_for(svc.main_loop(), timeout=2)


@pytest.mark.asyncio
async def test_signal_handler(process_base):
    process_base.exitevent = asyncio.Event()
    await process_base._signal_term_handler(15)
    assert process_base.exitevent.is_set()


def test_signal_handler_no_event(process_base):
    process_base.exitevent = None

    async def _run():
        await process_base._signal_term_handler(15)

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_do_waitexit_timeout_ok(process_base):
    process_base.exitevent = asyncio.Event()
    await process_base.do_waitexit(timeout=0.01)


# --- middleware ---


@pytest.mark.asyncio
async def test_middleware_setup_continue(ini_file):
    from pympacds.middleware import MiddlewareBase
    from pympacds.process import ProcessBase

    class TMW(MiddlewareBase):
        async def setup(self):
            raise ValueError("x")

        def on_error(self, e):
            return True

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("mw", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    svc._middleware_instances = [TMW(svc, None)]
    assert await svc._setup_middleware()
    assert len(svc._middleware_instances) == 0


@pytest.mark.asyncio
async def test_middleware_setup_abort(ini_file):
    from pympacds.middleware import MiddlewareBase
    from pympacds.process import ProcessBase

    class SMW(MiddlewareBase):
        async def setup(self):
            raise ValueError("x")

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("mw2", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    svc._middleware_instances = [SMW(svc, None)]
    assert not await svc._setup_middleware()


@pytest.mark.asyncio
async def test_middleware_teardown(ini_file):
    from pympacds.middleware import MiddlewareBase, MiddlewareSpec
    from pympacds.process import ProcessBase

    calls = []

    class TDMW(MiddlewareBase):
        async def teardown(self):
            calls.append(1)

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("mw3", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

        def register_middleware(self):
            return [MiddlewareSpec(TDMW, None)]

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert await svc.init_loop()
    svc.exitevent.set()
    await svc.close_loop()
    assert len(calls) == 1


# --- validation ---


def test_validation_int_max(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"n": {"type": "int", "max": 5}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {"n": "10"}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_validation_float_min(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"n": {"type": "float", "min": 0.0}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {"n": "-1.0"}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_validation_float_parse(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"n": {"type": "float"}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {"n": "abc"}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


def test_validation_schema_default(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"opt": {"type": "str", "default": "fallback"}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is True
    assert p.config["s"]["opt"] == "fallback"


def test_validation_schema_bad_json(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text("not json {")
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False


# --- logging ---


def test_logfile_with_suffix(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    logf = tmp_path / "app.log"
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["DEFAULT"]["logfile"] = str(logf)
    cp["DEFAULT"]["logfilesuffix"] = "testarg"
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    p.argparser.add_argument("--testarg", default="")
    p.setup(["-c", ini_file, "--testarg", "i1"])
    assert len(p.logger.handlers) >= 1


def test_logstdout_and_file(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    logf = tmp_path / "both.log"
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["DEFAULT"]["logfile"] = str(logf)
    cp["DEFAULT"]["logstdout"] = "true"
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    assert len(p.logger.handlers) >= 2


def test_logfile_fallback_tmp(ini_file, monkeypatch):
    import logging.handlers

    from pympacds.process import ProcessBase

    logf = "/root/nope/app.log"
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["DEFAULT"]["logfile"] = logf
    with open(ini_file, "w") as f:
        cp.write(f)
    orig = logging.handlers.RotatingFileHandler.__init__

    def fail_init(self, fn, *a, **kw):
        if "/root/" in fn:
            raise OSError("nope")
        orig(self, fn, *a, **kw)

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "__init__", fail_init)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is True


def test_logfile_mkdirs(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    logdir = tmp_path / "ld"
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["DEFAULT"]["logfile"] = str(logdir / "svc.log")
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    p.setup(["-c", ini_file])
    assert logdir.is_dir()


# ------------------------------------------------------------------
# cli.py — install, config, setup, validate
# ------------------------------------------------------------------


def test_cli_config_validate_ok(monkeypatch, tmp_path):
    cfg = tmp_path / "t.ini"
    cfg.write_text(
        "[DEFAULT]\nloglevel = WARN\nlogstdout = false\nlogfile =\n[dbus]\nbus_type = system\n"
    )
    monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "validate", "-c", str(cfg)])
    from pympacds.cli import main

    main()


def test_cli_config_validate_with_schema(monkeypatch, tmp_path):
    cfg = tmp_path / "t.ini"
    schema = tmp_path / "s.json"
    cfg.write_text("[DEFAULT]\nloglevel = WARN\nlogstdout = false\nlogfile =\n[m]\nk = v\n")
    schema.write_text(json.dumps({"m": {"keys": {"k": {"type": "str"}}}}))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pympacds-admin", "config", "validate", "-c", str(cfg), "-s", str(schema)],
    )
    from pympacds.cli import main

    main()


def test_cli_config_get_missing(monkeypatch, tmp_path):
    cfg = tmp_path / "t.ini"
    cfg.write_text("[DEFAULT]\nloglevel = WARN\nlogstdout = false\nlogfile =\n[x]\ny = z\n")
    monkeypatch.setattr(
        sys, "argv", ["pympacds-admin", "config", "get", "-c", str(cfg), "x", "MISSING"]
    )
    from pympacds.cli import main

    with pytest.raises(SystemExit):
        main()


def test_cli_config_set_no_file(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "set", "s", "k", "v"])
    from pympacds.cli import main

    with pytest.raises(SystemExit):
        main()


def test_cli_setup_warning(monkeypatch):
    from pympacds.cli import _cmd_setup

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    monkeypatch.setattr("pympacds.cli._run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(os, "makedirs", lambda *a, **kw: None)
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    class Args:
        resource_dir = "/x"
        bus_prefix = "t"
        no_dirs = False
        dry_run = False
        quiet = False

    _cmd_setup(Args)


def test_cli_setup_quiet(monkeypatch):
    from pympacds.cli import _cmd_setup

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    monkeypatch.setattr("pympacds.cli._run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(os, "makedirs", lambda *a, **kw: None)
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    class Args:
        resource_dir = "/x"
        bus_prefix = "t"
        no_dirs = False
        dry_run = False
        quiet = True

    _cmd_setup(Args)


def test_cli_setup_dry_run_template(monkeypatch, tmp_path):
    from pympacds.cli import _cmd_setup

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    src = tmp_path / "pympacds.conf.in"
    src.write_text("@BUS_PREFIX@")
    monkeypatch.setattr(os, "makedirs", lambda *a, **kw: None)

    class Args:
        resource_dir = str(tmp_path)
        bus_prefix = "t"
        no_dirs = True
        dry_run = True
        quiet = True

    _cmd_setup(Args)


def test_cli_setup_update(monkeypatch, tmp_path):
    from pympacds.cli import _cmd_setup

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    dst = tmp_path / "pympacds.conf"
    dst.write_text('<allow own_prefix="org.old"/>')
    monkeypatch.setattr(os.path, "exists", lambda p: str(p) == str(dst))
    monkeypatch.setattr(os, "makedirs", lambda *a, **kw: None)

    class Args:
        resource_dir = str(tmp_path)
        bus_prefix = "com.new"
        no_dirs = True
        dry_run = True
        quiet = True

    _cmd_setup(Args)


def test_cli_install_dry(monkeypatch, tmp_path):
    from pympacds.cli import _cmd_install

    d = tmp_path / "share"
    d.mkdir()
    (d / "pympacds-svc.service").write_text("x")
    monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(d / fn)))

    class Args:
        service_name = "svc"
        dry_run = True

    _cmd_install(Args)


def test_cli_install_full(monkeypatch, tmp_path):
    from pympacds.cli import _cmd_install

    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    d = tmp_path / "share"
    d.mkdir()
    (d / "pympacds-svc.service").write_text("x")
    monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(d / fn)))
    calls = []
    monkeypatch.setattr(
        "pympacds.cli._run",
        lambda *a, **kw: calls.append(a[0]) or type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr("pympacds.cli.shutil.copy", lambda *a: None)
    monkeypatch.setattr("pympacds.cli.os.path.exists", lambda p: False)

    class Args:
        service_name = "svc"
        dry_run = False
        no_enable = False
        start = True

    _cmd_install(Args)
    assert len(calls) >= 3


def test_cli_list_direct(session_bus_address, monkeypatch):
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address
    monkeypatch.setattr(sys, "argv", ["pympacds-admin", "list"])
    try:
        from pympacds.cli import main

        main()
    finally:
        os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)


def test_cli_config_show_empty(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config", "show", "-c", "/nonexistent"])
    from pympacds.cli import main

    main()


def test_cli_config_missing_sub(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pympacds-admin", "config"])
    from pympacds.cli import main

    main()


def test_cli_config_error_check_true(monkeypatch):
    from pympacds.cli import _run

    with pytest.raises(SystemExit):
        _run(["/nonexistent/cmd"], check=True, quiet=True)


# --- schema ---


def test_schema_bool_invalid(ini_file, tmp_path):
    from pympacds.process import ProcessBase

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"s": {"keys": {"f": {"type": "bool"}}}}))
    cp = configparser.ConfigParser()
    cp.read(ini_file)
    cp["dbus"]["schema_file"] = str(schema)
    cp["s"] = {"f": "notbool"}
    with open(ini_file, "w") as f:
        cp.write(f)
    p = ProcessBase("t", "1.0")
    assert p.setup(["-c", ini_file]) is False

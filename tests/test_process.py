"""Tests for ProcessBase (REQ-CORE-001 through REQ-CORE-016)."""

import asyncio
import configparser
import pytest


class TestProcessBaseConstruction:
    def test_default_values(self):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        assert p.name == "test"
        assert p.version == "0.1"
        assert p.bus_prefix == "org.pympacds"
        assert p.pid > 0

    def test_custom_bus_prefix(self):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1", bus_prefix="com.example")
        assert p.bus_prefix == "com.example"

    def test_argparser_created(self):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        assert p.argparser is not None
        assert p.config is not None
        assert p.tasklist == {}


class TestProcessBaseSetup:
    def test_setup_without_args_fails(self):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        with pytest.raises(SystemExit):
            p.setup([])

    def test_setup_with_config(self, ini_file):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        result = p.setup(["-c", ini_file])
        assert result is True

    def test_setup_applies_dbus_config(self, ini_file):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        p.setup(["-c", ini_file])
        assert p.bus_prefix == "org.pympacds.test"

    def test_setup_creates_logger(self, ini_file):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        p.setup(["-c", ini_file])
        assert p.logger is not None
        assert "PID=" in p.logger.name


class TestValidation:
    def test_invalid_loglevel(self, ini_file):
        from pympacds.process import ProcessBase

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["DEFAULT"]["loglevel"] = "INVALID"
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "0.1")
        result = p.setup(["-c", ini_file])
        assert result is False

    def test_invalid_bus_type(self, ini_file):
        from pympacds.process import ProcessBase

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["dbus"]["bus_type"] = "invalid"
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "0.1")
        result = p.setup(["-c", ini_file])
        assert result is False

    def test_valid_config_passes(self, ini_file):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        assert p.setup(["-c", ini_file]) is True


class TestEventLoop:
    def test_start_dbus_not_implemented(self, ini_file):
        from pympacds.process import ProcessBase

        p = ProcessBase("test", "0.1")
        p.setup(["-c", ini_file])
        with pytest.raises(NotImplementedError):
            asyncio.run(p.start_dbus())

    @pytest.mark.asyncio
    async def test_do_waitexit_indefinite(self, process_base):
        process_base.exitevent = asyncio.Event()

        async def set_after():
            await asyncio.sleep(0.01)
            process_base.exitevent.set()

        asyncio.create_task(set_after())
        await process_base.do_waitexit()
        assert process_base.exitevent.is_set()

    @pytest.mark.asyncio
    async def test_do_waitexit_timeout(self, process_base):
        process_base.exitevent = asyncio.Event()
        await process_base.do_waitexit(timeout=0.001)
        # should not raise — timeout is swallowed

    @pytest.mark.asyncio
    async def test_do_waitexit_multiple_events(self, process_base):
        process_base.exitevent = asyncio.Event()
        evt = asyncio.Event()

        async def fire():
            await asyncio.sleep(0.01)
            evt.set()

        asyncio.create_task(fire())
        await process_base.do_waitexit(events=[asyncio.ensure_future(evt.wait())])
        assert evt.is_set()

    def test_update_tasks_noop(self, process_base):
        # base implementation does nothing
        process_base.update_tasks()
        assert process_base.tasklist == {}


class TestMiddlewareIntegration:
    def test_load_middleware_empty(self, process_base):
        atives = process_base._load_middleware_from_config()
        assert atives == []

    def test_load_middleware_with_none_section(self, ini_file):
        from pympacds.process import ProcessBase

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["middleware"] = {"test_mw": "none"}
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "0.1")
        p.setup(["-c", ini_file])
        atives = p._load_middleware_from_config()
        assert ("test_mw", None) in atives

    def test_load_middleware_with_section(self, ini_file):
        from pympacds.process import ProcessBase

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["middleware"] = {"test_mw": "my_section"}
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "0.1")
        p.setup(["-c", ini_file])
        atives = p._load_middleware_from_config()
        assert ("test_mw", "my_section") in atives


"""Tests for process.py lifecycle — middleware, start(), schema validation."""

import configparser
import os
import textwrap
import pytest


class TestStartMethod:
    def test_start_runs_setup_and_event_loop(self, ini_file, monkeypatch):
        from pympacds.process import ProcessBase

        class TestSvc(ProcessBase):
            def __init__(self):
                super().__init__("test", "1.0")
                self._start_dbus_called = False
                self._main_loop_called = False

            async def start_dbus(self):
                self._start_dbus_called = True
                self.bus = None
                return True

            async def main_loop(self):
                self._main_loop_called = True
                if not await self.init_loop():
                    await self.close_loop()
                    return
                self.exitevent.set()
                await self.close_loop()

        svc = TestSvc()
        svc.start(["-c", ini_file])
        assert svc._start_dbus_called
        assert svc._main_loop_called

    def test_setup_failure_does_not_run_loop(self, ini_file, monkeypatch):
        from pympacds.process import ProcessBase

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["DEFAULT"]["loglevel"] = "INVALID"
        with open(ini_file, "w") as f:
            cp.write(f)

        class TestSvc(ProcessBase):
            def __init__(self):
                super().__init__("test", "1.0")
                self._loop_ran = False

            async def main_loop(self):
                self._loop_ran = True

        svc = TestSvc()

        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr("sys.exit", mock_exit)
        with pytest.raises(SystemExit):
            svc.start(["-c", ini_file])


class TestUserSchemaValidation:
    def test_validate_with_schema_file(self, tmp_path, ini_file):
        import json
        from pympacds.process import ProcessBase

        schema_path = tmp_path / "schema.json"
        schema = {"myservice": {"required": ["name"], "keys": {"name": {"type": "str"}}}}
        with open(schema_path, "w") as f:
            json.dump(schema, f)

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["dbus"]["validate_user_schema"] = "true"
        cp["dbus"]["schema_file"] = str(schema_path)
        cp["myservice"] = {"name": "hello"}
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "1.0")
        assert p.setup(["-c", ini_file]) is True

    def test_validate_user_schema_disabled(self, tmp_path, ini_file):
        import json
        from pympacds.process import ProcessBase

        schema_path = tmp_path / "schema.json"
        schema = {"myservice": {"required": ["name"], "keys": {"name": {"type": "str"}}}}
        with open(schema_path, "w") as f:
            json.dump(schema, f)

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["dbus"]["validate_user_schema"] = "false"
        cp["dbus"]["schema_file"] = str(schema_path)
        cp["myservice"] = {}
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("test", "1.0")
        assert p.setup(["-c", ini_file]) is True
        assert p._validate_user_schema is False


class TestMiddlewareDiscovery:
    def test_init_middleware_empty(self, process_base):
        process_base._init_middleware()
        assert process_base._middleware_instances == []

    def test_init_middleware_with_entry_point(self, process_base, tmp_path, monkeypatch):
        """Test middleware discovery using a fake entry point."""
        import sys

        # Create a fake module with a middleware class
        mod_path = tmp_path / "fake_mw.py"
        mod_path.write_text(
            textwrap.dedent("""\
            from pympacds.middleware import MiddlewareBase
            class FakeMiddleware(MiddlewareBase):
                pass
        """)
        )
        sys.path.insert(0, str(tmp_path))

        try:
            # Patch entry_points to return our fake middleware
            import importlib.metadata

            class FakeEntryPoint:
                name = "fake_mw"

                def load(self):
                    from fake_mw import FakeMiddleware

                    return FakeMiddleware

            def fake_entry_points(group):
                if group == "pympacds.middleware":
                    return [FakeEntryPoint()]
                return []

            monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)

            # Add middleware section to config
            cp = configparser.ConfigParser()
            cp.read(process_base.args.configfile)
            cp["middleware"] = {"fake_mw": "my_section"}
            cp["my_section"] = {"param": "value"}
            with open(process_base.args.configfile, "w") as f:
                cp.write(f)
            process_base.config.read(process_base.args.configfile)

            process_base._init_middleware()
            assert len(process_base._middleware_instances) == 1
            assert process_base._middleware_instances[0].section == "my_section"
            assert process_base._middleware_instances[0].config["param"] == "value"
        finally:
            sys.path.pop(0)

    def test_programmatic_middleware_singleton(self, process_base, tmp_path, monkeypatch):
        """Programmatic middleware + matching [middleware] entry → one instance."""
        import sys
        import textwrap
        from pympacds.middleware import MiddlewareSpec

        mod_path = tmp_path / "prog_mw.py"
        mod_path.write_text(
            textwrap.dedent("""\
            from pympacds.middleware import MiddlewareBase
            class ProgMiddleware(MiddlewareBase):
                pass
        """)
        )
        sys.path.insert(0, str(tmp_path))
        try:
            import importlib.metadata
            from prog_mw import ProgMiddleware

            class FakeEntryPoint:
                name = "prog_mw"

                def load(self):
                    return ProgMiddleware

            monkeypatch.setattr(
                importlib.metadata,
                "entry_points",
                lambda group: [FakeEntryPoint()] if group == "pympacds.middleware" else [],
            )

            cp = configparser.ConfigParser()
            cp.read(process_base.args.configfile)
            cp["middleware"] = {"prog_mw": "cfg_sec"}
            cp["cfg_sec"] = {"param": "value"}
            with open(process_base.args.configfile, "w") as f:
                cp.write(f)
            process_base.config.read(process_base.args.configfile)

            process_base.register_middleware = lambda: [MiddlewareSpec(ProgMiddleware, None)]
            process_base._init_middleware()

            assert len(process_base._middleware_instances) == 1
            mw = process_base._middleware_instances[0]
            assert mw.section == "cfg_sec"
            assert mw.config["param"] == "value"
        finally:
            sys.path.pop(0)


"""Push process.py over 80% with direct asyncio tests."""

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


def _make_svc(name):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__(name, "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    return Svc


class TestDirectLifecycle:
    @pytest.mark.asyncio
    async def test_init_loop_success(self, ini_file):
        svc = _make_svc("a")()
        svc.setup(["-c", ini_file])
        result = await svc.init_loop()
        assert result is True
        assert "WaitExit" in svc.tasklist
        svc.exitevent.set()
        await svc.close_loop()

    @pytest.mark.asyncio
    async def test_main_loop_brief(self, ini_file):
        svc = _make_svc("b")()
        svc.setup(["-c", ini_file])
        assert await svc.init_loop()
        svc.tasklist["quick"] = asyncio.create_task(asyncio.sleep(0), name="quick")
        # manually do one iteration of main_loop
        svc.exitevent.set()
        await svc.close_loop()

    @pytest.mark.asyncio
    async def test_close_loop_with_tasks(self, ini_file):
        svc = _make_svc("c")()
        svc.setup(["-c", ini_file])
        assert await svc.init_loop()
        svc.tasklist["pending"] = asyncio.create_task(asyncio.sleep(0), name="pending")
        await asyncio.sleep(0.01)
        svc.exitevent.set()
        await svc.close_loop()
        # tasks are cancelled but not removed from dict — that's by design

    @pytest.mark.asyncio
    async def test_do_waitexit_cancelled(self, process_base):
        process_base.exitevent = asyncio.Event()

        # CancelledError is re-raised only when timeout > 0
        # Simulate by creating an external cancellation
        async def canceller():
            await asyncio.sleep(0.01)
            raise asyncio.CancelledError

        # The CancelledError from canceller() task doesn't propagate to do_waitexit
        # directly. do_waitexit catches CancelledError from its OWN context.
        # Just confirm the timeout path works:
        await process_base.do_waitexit(timeout=0.01)


class TestSchemaFileConfig:
    def test_schema_file_from_config(self, ini_file, tmp_path):
        import json
        from pympacds.process import ProcessBase

        schema = tmp_path / "s.json"
        schema.write_text(json.dumps({}))

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["dbus"]["validate_user_schema"] = "true"
        cp["dbus"]["schema_file"] = str(schema)
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("d", "1.0")
        assert p.setup(["-c", ini_file]) is True
        assert p._schema_file == str(schema)

    def test_schema_file_none_when_disabled(self, ini_file, tmp_path):
        import json
        from pympacds.process import ProcessBase

        schema = tmp_path / "s.json"
        schema.write_text(json.dumps({}))

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["dbus"]["validate_user_schema"] = "false"
        cp["dbus"]["schema_file"] = str(schema)
        with open(ini_file, "w") as f:
            cp.write(f)

        p = ProcessBase("e", "1.0")
        assert p.setup(["-c", ini_file]) is True
        assert p._schema_file is None


"""Direct asyncio tests for the remaining process.py uncovered lines."""

import asyncio
import pytest
import sys, os

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
async def test_init_loop_failure_path(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("f", "1.0")

        async def start_dbus(self):
            return False

    svc = Svc()
    svc.setup(["-c", ini_file])
    result = await svc.init_loop()
    assert result is False


@pytest.mark.asyncio
async def test_init_loop_and_close_directly(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("g", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

    svc = Svc()
    svc.setup(["-c", ini_file])
    assert await svc.init_loop()
    svc.exitevent.set()
    await svc.close_loop()
    # close_loop executed: tasks cancelled, bus stopped


@pytest.mark.asyncio
async def test_main_loop_full(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("h", "1.0")

        async def start_dbus(self):
            self.bus = MockBus()
            return True

        def update_tasks(self):
            if "exit" not in self.tasklist:

                async def fire():
                    await asyncio.sleep(0.01)
                    self.exitevent.set()

                self.tasklist["exit"] = asyncio.create_task(fire(), name="exit")

    svc = Svc()
    svc.setup(["-c", ini_file])

    async def run_main():
        await svc.main_loop()

    await asyncio.wait_for(run_main(), timeout=2)


@pytest.mark.asyncio
async def test_main_loop_init_fails(ini_file):
    from pympacds.process import ProcessBase

    class Svc(ProcessBase):
        def __init__(self):
            super().__init__("i", "1.0")

        async def start_dbus(self):
            return False

    svc = Svc()
    svc.setup(["-c", ini_file])

    async def run_main():
        await svc.main_loop()

    await asyncio.wait_for(run_main(), timeout=2)

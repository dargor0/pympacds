"""Tests for MiddlewareBase (REQ-MIDW-001)."""

import pytest


class TestMiddlewareBase:
    def test_construction(self, process_base):
        from pympacds.middleware import MiddlewareBase

        mw = MiddlewareBase(process_base, "test_section")
        assert mw.service is process_base
        assert mw.section == "test_section"

    def test_construction_with_none_section(self, process_base):
        from pympacds.middleware import MiddlewareBase

        mw = MiddlewareBase(process_base, None)
        assert mw.section is None
        assert mw._config == {}

    @pytest.mark.asyncio
    async def test_default_setup_is_noop(self, process_base):
        from pympacds.middleware import MiddlewareBase

        mw = MiddlewareBase(process_base, None)
        await mw.setup()
        # no exception = pass

    @pytest.mark.asyncio
    async def test_default_teardown_is_noop(self, process_base):
        from pympacds.middleware import MiddlewareBase

        mw = MiddlewareBase(process_base, None)
        await mw.teardown()
        # no exception = pass

    def test_default_on_error_returns_false(self, process_base):
        from pympacds.middleware import MiddlewareBase

        mw = MiddlewareBase(process_base, None)
        assert mw.on_error(ValueError("test")) is False

    def test_custom_on_error(self, process_base):
        from pympacds.middleware import MiddlewareBase

        class TolerantMiddleware(MiddlewareBase):
            def on_error(self, error):
                return True

        mw = TolerantMiddleware(process_base, None)
        assert mw.on_error(ValueError("test")) is True
"""Tests for builtin middleware (REQ-MIDW-002)."""

import configparser
import io
import json
import pytest
import urllib.error


class TestHttpConfigMiddleware:
    def test_construction(self, process_base):
        from pympacds.middleware import HttpConfigMiddleware

        mw = HttpConfigMiddleware(process_base, "http_section")
        assert mw.section == "http_section"

    @pytest.mark.asyncio
    async def test_skip_when_no_url(self, process_base):
        from pympacds.middleware import HttpConfigMiddleware

        mw = HttpConfigMiddleware(process_base, "http_section")
        # no url in config — should skip silently
        await mw.setup()
        # no exception, no restart = pass

    @pytest.mark.asyncio
    async def test_fetch_failure_continues(self, process_base, ini_file, monkeypatch):
        from pympacds.middleware import HttpConfigMiddleware
        import urllib.request

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["http_section"] = {
            "url": "http://localhost:19999/nonexistent",
            "timeout_s": "1",
        }
        with open(ini_file, "w") as f:
            cp.write(f)

        process_base.setup(["-c", ini_file])
        mw = HttpConfigMiddleware(process_base, "http_section")

        def mock_urlopen(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        await mw.setup()
        # should not raise and not trigger restart
        assert process_base.exitevent is None or not process_base.exitevent.is_set()

    @pytest.mark.asyncio
    async def test_config_unchanged(self, process_base, ini_file, monkeypatch):
        from pympacds.middleware import HttpConfigMiddleware
        import urllib.request

        cp = configparser.ConfigParser()
        cp.read(ini_file)
        cp["http_section"] = {"url": "http://example.com/cfg"}
        with open(ini_file, "w") as f:
            cp.write(f)

        process_base.setup(["-c", ini_file])
        mw = HttpConfigMiddleware(process_base, "http_section")

        # return the current config as JSON
        current = {
            s: dict(process_base.config[s])
            for s in process_base.config.sections()
        }
        current_json = json.dumps(current, sort_keys=True).encode()

        def mock_urlopen(*args, **kwargs):
            return io.BytesIO(current_json)

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        await mw.setup()
        # unchanged — no restart
        assert process_base.exitevent is None or not process_base.exitevent.is_set()

    @pytest.mark.asyncio
    async def test_config_changed_triggers_restart(
        self, process_base, ini_file, tmp_path, monkeypatch
    ):
        from pympacds.middleware import HttpConfigMiddleware
        import urllib.request

        # Use a tmp_path config file so the write goes to a known location
        cfg_path = str(tmp_path / "test.ini")
        cp = configparser.ConfigParser()
        cp["DEFAULT"] = {"loglevel": "WARN", "logstdout": "false", "logfile": ""}
        cp["dbus"] = {"bus_type": "system", "bus_prefix": "org.test"}
        cp["http_section"] = {"url": "http://example.com/cfg"}
        with open(cfg_path, "w") as f:
            cp.write(f)

        process_base.setup(["-c", cfg_path])
        process_base.exitevent = None  # will be set during init_loop normally
        mw = HttpConfigMiddleware(process_base, "http_section")

        new_config = {"DEFAULT": {"loglevel": "DEBUG"}}
        new_json = json.dumps(new_config, sort_keys=True).encode()

        def mock_urlopen(*args, **kwargs):
            return io.BytesIO(new_json)

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        import asyncio

        process_base.exitevent = asyncio.Event()
        await mw.setup()
        # config changed — should trigger restart
        assert process_base.exitevent.is_set()

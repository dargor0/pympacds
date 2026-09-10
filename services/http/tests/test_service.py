"""Service-level unit tests (REQ-HTTP-007)."""

import asyncio
import json
import logging
import ssl

import pytest
from helpers import FailingResponse, FakeResponse, make_config

from pympacds_http.service import HttpService

logger = logging.getLogger("test_service")
logger.setLevel(logging.CRITICAL)


class FakeContract:
    def __init__(self):
        self.completed = []

    def request_completed(self, token, result):
        self.completed.append((token, result))


def _service(fake_session, **cfg):
    svc = HttpService(session_factory=lambda s: fake_session)
    svc.logger.setLevel(logging.CRITICAL)
    svc.config["http"] = make_config(**cfg)
    svc._build_session()
    svc._contract = FakeContract()
    return svc


async def _drain(svc, timeout=1.0):
    for _ in range(int(timeout * 200)):
        if not svc._tasks:
            return
        await asyncio.sleep(0.005)


class TestSend:
    @pytest.mark.asyncio
    async def test_send_returns_token_and_emits_completed(self, fake_session):
        svc = _service(fake_session)
        token = svc.dbus_http_send("hello")
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)
        await _drain(svc)
        assert len(svc._contract.completed) == 1
        tok, result = svc._contract.completed[0]
        assert tok == token
        data = json.loads(result)
        assert data["status_code"] == 200
        assert data["body"] == "ok"
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_send_without_url_emits_error(self, fake_session):
        svc = _service(fake_session, default_url="")
        svc.dbus_http_send("hello")
        await _drain(svc)
        data = json.loads(svc._contract.completed[0][1])
        assert data["status_code"] is None
        assert data["error"] == "no URL configured"
        assert fake_session.requests == []

    @pytest.mark.asyncio
    async def test_send_uses_defaults(self, fake_session):
        svc = _service(fake_session, default_method="PUT", default_headers='{"X-A": "b"}')
        svc.dbus_http_send("body")
        await _drain(svc)
        method, url, headers, data, _ = fake_session.requests[0]
        assert method == "PUT"
        assert url == "http://example.test/"
        assert headers == {"X-A": "b"}
        assert data == "body"


class TestDownload:
    @pytest.mark.asyncio
    async def test_download_writes_file(self, fake_session, tmp_path):
        svc = _service(fake_session)
        dest = tmp_path / "out.bin"
        svc.dbus_http_download("hello", str(dest))
        await _drain(svc)
        data = json.loads(svc._contract.completed[0][1])
        assert data["body"] is None
        assert data["download_path"] == str(dest)
        assert data["bytes_written"] == 2  # b"ok"
        assert dest.read_bytes() == b"ok"


class TestRequest:
    @pytest.mark.asyncio
    async def test_request_passes_explicit_args(self, fake_session):
        svc = _service(fake_session)
        svc.dbus_http_request("GET", "http://x.test/", '{"K": "v"}', "thebody", "")
        await _drain(svc)
        method, url, headers, data, _ = fake_session.requests[0]
        assert method == "GET"
        assert url == "http://x.test/"
        assert headers == {"K": "v"}
        assert data == "thebody"
        result = json.loads(svc._contract.completed[0][1])
        assert result["body"] == "ok"  # empty download_path -> body returned


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_then_success(self, fake_session):
        fake_session.responses = [FakeResponse(500), FakeResponse(200)]
        svc = _service(fake_session, retries="3", retry_backoff_s="0")
        svc.dbus_http_send("hello")
        await _drain(svc)
        assert len(fake_session.requests) == 2
        data = json.loads(svc._contract.completed[0][1])
        assert data["status_code"] == 200

    @pytest.mark.asyncio
    async def test_retries_exhausted(self, fake_session):
        fake_session.responses = [FakeResponse(500)] * 4
        svc = _service(fake_session, retries="3", retry_backoff_s="0")
        svc.dbus_http_send("hello")
        await _drain(svc)
        assert len(fake_session.requests) == 4
        data = json.loads(svc._contract.completed[0][1])
        assert data["status_code"] == 500
        assert data["error"]

    @pytest.mark.asyncio
    async def test_network_error_retried(self, fake_session):
        fake_session.responses = [FailingResponse(), FakeResponse(200)]
        svc = _service(fake_session, retries="3", retry_backoff_s="0")
        svc.dbus_http_send("hello")
        await _drain(svc)
        assert len(fake_session.requests) == 2
        data = json.loads(svc._contract.completed[0][1])
        assert data["status_code"] == 200

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self, fake_session):
        fake_session.responses = [FakeResponse(404)]
        svc = _service(fake_session, retries="3", retry_backoff_s="0")
        svc.dbus_http_send("hello")
        await _drain(svc)
        assert len(fake_session.requests) == 1
        data = json.loads(svc._contract.completed[0][1])
        assert data["status_code"] == 404
        assert data["error"] is None


class TestResultEncoding:
    @pytest.mark.asyncio
    async def test_result_has_all_fields(self, fake_session):
        svc = _service(fake_session)
        svc.dbus_http_send("hello")
        await _drain(svc)
        data = json.loads(svc._contract.completed[0][1])
        assert set(data) == {
            "status_code",
            "url",
            "elapsed_ms",
            "body",
            "download_path",
            "bytes_written",
            "error",
        }


class TestHeaders:
    def test_parse_headers_empty(self, fake_session):
        svc = _service(fake_session)
        assert svc._parse_headers("") == {}

    def test_parse_headers_json(self, fake_session):
        svc = _service(fake_session)
        assert svc._parse_headers('{"A": "1"}') == {"A": "1"}

    def test_parse_headers_invalid(self, fake_session):
        svc = _service(fake_session)
        assert svc._parse_headers("not json") == {}

    def test_parse_headers_non_object(self, fake_session):
        svc = _service(fake_session)
        assert svc._parse_headers("[1,2]") == {}


class TestTls:
    def test_ssl_context_default(self, fake_session):
        svc = _service(fake_session)
        assert svc._build_ssl_context() is True

    def test_ssl_context_disabled(self, fake_session):
        svc = _service(fake_session, tls_verify="false")
        assert svc._build_ssl_context() is False

    def test_ssl_context_mutual_tls(self, fake_session, monkeypatch):
        svc = _service(fake_session, tls_certfile="/c.pem", tls_keyfile="/k.pem")
        calls = {}

        class FakeCtx:
            def __init__(self):
                self.check_hostname = True
                self.verify_mode = None

            def load_cert_chain(self, certfile=None, keyfile=None):
                calls["load"] = (certfile, keyfile)

        monkeypatch.setattr(ssl, "create_default_context", FakeCtx)

        ctx = svc._build_ssl_context()
        assert isinstance(ctx, FakeCtx)
        assert calls["load"] == ("/c.pem", "/k.pem")
        assert ctx.check_hostname is True

    def test_ssl_context_mutual_tls_no_verify(self, fake_session, monkeypatch):
        svc = _service(
            fake_session,
            tls_verify="false",
            tls_certfile="/c.pem",
            tls_keyfile="/k.pem",
        )

        class FakeCtx:
            def __init__(self):
                self.check_hostname = True
                self.verify_mode = None

            def load_cert_chain(self, certfile=None, keyfile=None):
                pass

        monkeypatch.setattr(ssl, "create_default_context", FakeCtx)

        ctx = svc._build_ssl_context()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_tls_pair_requires_both(self, fake_session):
        svc = _service(fake_session, tls_certfile="/c.pem")
        assert svc._validate_tls_pair() is False

    def test_tls_pair_ok_when_both(self, fake_session):
        svc = _service(fake_session, tls_certfile="/c.pem", tls_keyfile="/k.pem")
        assert svc._validate_tls_pair() is True

    def test_tls_pair_ok_when_neither(self, fake_session):
        svc = _service(fake_session)
        assert svc._validate_tls_pair() is True


class TestConfig:
    def test_get_set(self, fake_session):
        svc = _service(fake_session)
        assert svc.dbus_config_set("http", "retries", "5") is True
        assert svc.dbus_config_get("http", "retries") == "5"

    def test_get_missing_section(self, fake_session):
        svc = _service(fake_session)
        assert svc.dbus_config_get("nope", "key") == ""

    def test_set_creates_section(self, fake_session):
        svc = _service(fake_session)
        assert svc.dbus_config_set("newsection", "key", "value") is True
        assert svc.dbus_config_get("newsection", "key") == "value"

    def test_set_emits_changed(self, fake_session):
        svc = _service(fake_session)

        class FakeCfg:
            def __init__(self):
                self.changed = []

            def config_changed(self, s, k, v):
                self.changed.append((s, k, v))

        svc._config_contract = FakeCfg()
        assert svc.dbus_config_set("http", "retries", "9") is True
        assert svc._config_contract.changed == [("http", "retries", "9")]


class TestHealth:
    def test_ping(self, fake_session):
        svc = _service(fake_session)
        assert svc.dbus_health_ping() is True

    def test_status_includes_tags(self, fake_session):
        svc = _service(fake_session)
        data = json.loads(svc.dbus_health_status())
        assert "provides" in data
        assert "requires" in data


class TestSessionBuild:
    def test_build_session_with_factory(self, fake_session):
        svc = HttpService(session_factory=lambda s: fake_session)
        svc.config["http"] = make_config()
        svc._build_session()
        assert svc._session is fake_session

    def test_build_session_real(self, monkeypatch):
        import pympacds_http.service as svc_mod

        calls = {}

        class CT:
            def __init__(self, total):
                calls["timeout"] = total

        class TC:
            def __init__(self, ssl):
                calls["ssl"] = ssl

        class CS:
            def __init__(self, timeout=None, connector=None):
                calls["session"] = (timeout, connector)

            async def close(self):
                pass

        monkeypatch.setattr(svc_mod.aiohttp, "ClientTimeout", CT)
        monkeypatch.setattr(svc_mod.aiohttp, "TCPConnector", TC)
        monkeypatch.setattr(svc_mod.aiohttp, "ClientSession", CS)

        svc = HttpService()
        svc.config["http"] = make_config(timeout_s="7")
        svc._build_session()
        assert calls["timeout"] == 7
        assert calls["ssl"] is True


class TestCloseLoop:
    @pytest.mark.asyncio
    async def test_close_loop_closes_session(self, fake_session):
        svc = _service(fake_session)
        await svc.close_loop()
        assert fake_session.closed is True
        assert svc._session is None


class TestStartDbus:
    @pytest.mark.asyncio
    async def test_start_dbus_wires_contracts(self, monkeypatch):
        import pympacds_http.service as svc_mod

        calls = {}

        class FakeBus:
            def __init__(self, **kwargs):
                calls["bus"] = kwargs

            def add_interface(self, path, iface):
                calls.setdefault("interfaces", []).append(path)

            async def start(self):
                calls["started"] = True

        class FakeContract:
            def __init__(self, ifname, base):
                calls["contract"] = ifname

        class FakeHealth:
            def __init__(self, ifname, base):
                calls["health"] = ifname

        class FakeConfig:
            def __init__(self, ifname, base):
                calls["config"] = ifname

        class FakeSession:
            async def close(self):
                pass

        monkeypatch.setattr(svc_mod, "DBusManager", FakeBus)
        monkeypatch.setattr(svc_mod, "HttpContract", FakeContract)
        monkeypatch.setattr(svc_mod, "HealthContract", FakeHealth)
        monkeypatch.setattr(svc_mod, "ConfigContract", FakeConfig)

        svc = HttpService(session_factory=lambda s: FakeSession())
        svc.logger.setLevel(logging.CRITICAL)
        svc.config["http"] = make_config()
        svc.config["dbus"] = {"contract_health": "true", "contract_config": "true"}

        assert await svc.start_dbus() is True
        assert calls["started"] is True
        assert calls["contract"] == f"{svc.bus_prefix}.HTTP"
        assert calls["health"] == f"{svc.bus_prefix}.Health"
        assert calls["config"] == f"{svc.bus_prefix}.Config"
        assert svc._session is not None

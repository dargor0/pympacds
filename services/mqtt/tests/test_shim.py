"""Shim unit tests (REQ-MQTT-SHIM-*)."""

import asyncio
import logging
import socket

import pytest
from helpers import FakeLoop, make_config

from pympacds_mqtt.shim import MqttShim

logger = logging.getLogger("test_shim")
logger.setLevel(logging.CRITICAL)


def _shim(fake_client, **overrides):
    return MqttShim(make_config(**overrides), logger, client_factory=lambda: fake_client)


async def _fake_getaddrinfo(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port))]


def _patch_loop(monkeypatch):
    """Neutralize DNS + socket-registration on the live loop for connect tests."""
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(loop, "add_reader", lambda fd, cb: None)
    monkeypatch.setattr(loop, "add_writer", lambda fd, cb: None)
    monkeypatch.setattr(loop, "remove_reader", lambda fd: None)
    monkeypatch.setattr(loop, "remove_writer", lambda fd: None)


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, fake_client, monkeypatch):
        shim = _shim(fake_client)
        _patch_loop(monkeypatch)

        task = asyncio.create_task(shim.connect())
        await asyncio.sleep(0)
        fake_client.emit_connect()

        assert await task is True
        assert shim.connected is True
        assert ("connect_async", "192.0.2.10", 1883, 60) in fake_client.calls

    @pytest.mark.asyncio
    async def test_connect_fail_via_callback(self, fake_client, monkeypatch):
        shim = _shim(fake_client)
        _patch_loop(monkeypatch)

        task = asyncio.create_task(shim.connect())
        await asyncio.sleep(0)
        fake_client.emit_connect_fail()

        assert await task is False
        assert shim.connected is False
        await shim.stop()

    @pytest.mark.asyncio
    async def test_connect_timeout(self, fake_client, monkeypatch):
        shim = _shim(fake_client, connect_timeout_s="0.05")
        _patch_loop(monkeypatch)

        assert await shim.connect() is False
        assert shim.connected is False
        await shim.stop()


class TestSocketIntegration:
    def test_socket_open_registers_reader(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop

        shim._on_socket_open(fake_client, None, fake_client.sock)

        assert loop.readers == {42: fake_client.loop_read}

    def test_socket_open_registers_writer_when_want_write(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop
        fake_client._want_write = True

        shim._on_socket_open(fake_client, None, fake_client.sock)

        assert loop.readers == {42: fake_client.loop_read}
        assert loop.writers == {42: fake_client.loop_write}

    def test_socket_close_removes_reader(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop
        shim._on_socket_open(fake_client, None, fake_client.sock)

        shim._on_socket_close(fake_client, None, fake_client.sock)

        assert loop.readers == {}

    def test_socket_register_write_adds_writer(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop

        shim._on_socket_register_write(fake_client, None, fake_client.sock)

        assert loop.writers == {42: fake_client.loop_write}

    def test_socket_unregister_write_removes_writer(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop
        shim._on_socket_register_write(fake_client, None, fake_client.sock)

        shim._on_socket_unregister_write(fake_client, None, fake_client.sock)

        assert loop.writers == {}


class TestMessageBridge:
    @pytest.mark.asyncio
    async def test_on_message_enqueues(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        fake_client.emit_message("sensors/1", b"42")
        assert await asyncio.wait_for(shim._messages.get(), timeout=0.1) == ("sensors/1", b"42")

    def test_overflow_drops_oldest(self, fake_client):
        shim = _shim(fake_client, queue_size="2")
        shim._ensure_client()
        fake_client.emit_message("a", b"1")
        fake_client.emit_message("b", b"2")
        fake_client.emit_message("c", b"3")  # drops "a"
        assert shim._messages.get_nowait() == ("b", b"2")
        assert shim._messages.get_nowait() == ("c", b"3")
        assert shim._messages.empty()

    def test_unbounded_queue(self, fake_client):
        shim = _shim(fake_client, queue_size="0")
        shim._ensure_client()
        for i in range(5):
            fake_client.emit_message(f"t{i}", b"x")
        assert shim._messages.qsize() == 5


class TestOperations:
    def test_publish_requires_connection(self, fake_client):
        shim = _shim(fake_client)
        assert shim.publish("t", "payload") is False

    def test_subscribe_requires_connection(self, fake_client):
        shim = _shim(fake_client)
        assert shim.subscribe("t") is False

    def test_unsubscribe_requires_connection(self, fake_client):
        shim = _shim(fake_client)
        assert shim.unsubscribe("t") is False


class TestTls:
    def test_tls_standard_path(self, fake_client):
        shim = _shim(
            fake_client,
            use_tls="true",
            tls_ca="/ca.pem",
            tls_certfile="/c.pem",
            tls_keyfile="/k.pem",
        )
        shim._ensure_client()
        assert any(c[0] == "tls_set" for c in fake_client.calls)

    def test_tls_insecure(self, fake_client):
        shim = _shim(fake_client, use_tls="true", tls_insecure="true")
        shim._ensure_client()
        assert ("tls_insecure_set", True) in fake_client.calls

    def test_custom_context(self, fake_client):
        ctx = object()

        class Custom(MqttShim):
            def build_ssl_context(self):
                return ctx

        shim = Custom(make_config(use_tls="true"), logger, client_factory=lambda: fake_client)
        shim._ensure_client()
        assert ("tls_set_context", ctx) in fake_client.calls


class TestReconnect:
    @pytest.mark.asyncio
    async def test_disconnect_triggers_reconnect(self, fake_client, monkeypatch):
        shim = _shim(fake_client, reconnect_min_s="1", reconnect_max_s="2")
        _patch_loop(monkeypatch)

        task = asyncio.create_task(shim.connect())
        await asyncio.sleep(0)
        fake_client.emit_connect()
        await task
        assert shim.connected

        fake_client.emit_disconnect()
        assert shim.connected is False
        assert shim._reconnect_task is not None
        await shim.stop()

    @pytest.mark.asyncio
    async def test_graceful_disconnect_no_reconnect(self, fake_client, monkeypatch):
        shim = _shim(fake_client)
        _patch_loop(monkeypatch)

        task = asyncio.create_task(shim.connect())
        await asyncio.sleep(0)
        fake_client.emit_connect()
        await task

        await shim.disconnect()
        fake_client.emit_disconnect()
        assert shim.connected is False
        assert shim._reconnect_task is None


class TestStatus:
    def test_status_fields(self, fake_client):
        shim = _shim(fake_client)
        status = shim.status()
        assert status["connected"] is False
        assert status["protocol_version"] == "3.1.1"
        assert status["reconnect_attempts"] == 0
        assert status["subscribed_topics"] == []


def _connected_shim(fake_client, **overrides):
    shim = _shim(fake_client, **overrides)
    shim._ensure_client()
    shim._connected.set()
    return shim


class TestOperationsConnected:
    def test_subscribe_success(self, fake_client):
        shim = _connected_shim(fake_client)
        assert shim.subscribe("t") is True
        assert ("subscribe", "t", 1) in fake_client.calls
        assert "t" in shim._subscribed

    def test_subscribe_custom_qos(self, fake_client):
        shim = _connected_shim(fake_client)
        assert shim.subscribe("t", qos=2) is True
        assert ("subscribe", "t", 2) in fake_client.calls

    def test_unsubscribe_success(self, fake_client):
        shim = _connected_shim(fake_client)
        shim._subscribed = {"t"}
        assert shim.unsubscribe("t") is True
        assert "t" not in shim._subscribed

    def test_publish_success(self, fake_client):
        shim = _connected_shim(fake_client)
        assert shim.publish("t", "payload") is True
        assert ("publish", "t", "payload", 1) in fake_client.calls


class TestResubscribe:
    @pytest.mark.asyncio
    async def test_resubscribe(self, fake_client):
        shim = _connected_shim(fake_client)
        shim._subscribed = {"a", "b"}
        await shim._resubscribe()
        assert shim._subscribed == {"a", "b"}
        assert ("subscribe", "a", 1) in fake_client.calls
        assert ("subscribe", "b", 1) in fake_client.calls


class TestMessages:
    @pytest.mark.asyncio
    async def test_messages_iterator(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        fake_client.emit_message("t", b"p")
        gen = shim.messages()
        msg = await asyncio.wait_for(gen.__anext__(), timeout=0.1)
        assert msg == ("t", b"p")


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_run_loop_calls_loop_misc(self, fake_client):
        shim = _shim(fake_client, loop_interval_s="0.01")
        shim._ensure_client()
        task = asyncio.create_task(shim.run_loop())
        await asyncio.sleep(0.03)
        task.cancel()
        await task
        assert any(c[0] == "loop_misc" for c in fake_client.calls)


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_reconnect_loop_breaks_on_connect(self, fake_client):
        shim = _shim(fake_client, reconnect_min_s="0", reconnect_max_s="1")
        shim._ensure_client()

        async def fake_connect():
            return True

        shim.connect = fake_connect
        task = asyncio.create_task(shim._reconnect_loop())
        await asyncio.wait_for(task, timeout=1)
        assert shim._reconnect_attempts >= 1


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_publishes_lwt_and_unsubscribes(self, fake_client):
        shim = _connected_shim(fake_client, lwt_topic="lwt", lwt_payload="bye")
        shim._subscribed = {"a", "b"}
        await shim.stop()
        assert ("publish", "lwt", "bye", 0) in fake_client.calls
        assert ("unsubscribe", "a") in fake_client.calls
        assert ("unsubscribe", "b") in fake_client.calls
        assert shim.connected is False


class TestBuildClientConfig:
    def test_username_and_lwt(self, fake_client):
        shim = _shim(fake_client, username="u", password="p", lwt_topic="lwt", lwt_payload="bye")
        shim._ensure_client()
        assert ("username_pw_set", "u", "p") in fake_client.calls
        assert ("will_set", "lwt", "bye", 0) in fake_client.calls


class TestCfgHelpers:
    def test_cfg_attribute_error(self):
        shim = MqttShim(None, logger, client_factory=lambda: object())
        assert shim._cfg("host", "default") == "default"

    def test_cfg_float_invalid(self):
        shim = MqttShim({"loop_interval_s": "abc"}, logger, client_factory=lambda: object())
        assert shim._cfg_float("loop_interval_s", 1.0) == 1.0

    def test_cfg_int_invalid(self):
        shim = MqttShim({"port": "abc"}, logger, client_factory=lambda: object())
        assert shim._cfg_int("port", 1883) == 1883


class TestConnectRejected:
    @pytest.mark.asyncio
    async def test_on_connect_rejected(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        shim._connect_result = asyncio.get_running_loop().create_future()

        class RC:
            is_failure = True

        fake_client.emit_connect(reason_code=RC())
        assert shim._connect_result.result() is False
        assert shim.connected is False


class TestSocketEdgeCases:
    def test_socket_close_removes_writer_when_want_write(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop
        fake_client._want_write = True
        shim._on_socket_open(fake_client, None, fake_client.sock)
        shim._on_socket_close(fake_client, None, fake_client.sock)
        assert loop.readers == {}
        assert loop.writers == {}

    def test_socket_open_getpeername_oserror(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()
        loop = FakeLoop()
        shim._loop = loop

        class BadSocket:
            def fileno(self):
                return 42

            def getpeername(self):
                raise OSError("nope")

        shim._on_socket_open(fake_client, None, BadSocket())
        assert shim._peer == (None, None)


class TestDisconnectError:
    @pytest.mark.asyncio
    async def test_disconnect_error_is_logged(self, fake_client, monkeypatch):
        shim = _shim(fake_client)
        shim._ensure_client()

        def bad_disconnect():
            raise RuntimeError("boom")

        monkeypatch.setattr(fake_client, "disconnect", bad_disconnect)
        await shim.disconnect()
        assert shim.connected is False


class TestReconnectMethod:
    @pytest.mark.asyncio
    async def test_reconnect_rebuilds_and_connects(self, fake_client):
        shim = _shim(fake_client)
        shim._ensure_client()

        async def fake_connect():
            return True

        shim.connect = fake_connect
        assert await shim.reconnect() is True
        assert ("disconnect",) in fake_client.calls


class TestBlockingPrevention:
    @pytest.mark.asyncio
    async def test_no_blocking_primitives(self, fake_client, monkeypatch):
        blocked = []

        def _forbidden(*args, **kwargs):
            blocked.append(args)
            raise AssertionError("blocking primitive called")

        monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
        monkeypatch.setattr(socket, "create_connection", _forbidden)

        shim = _shim(fake_client)
        _patch_loop(monkeypatch)

        task = asyncio.create_task(shim.connect())
        await asyncio.sleep(0)
        fake_client.emit_connect()
        assert await task is True
        assert blocked == []

    @pytest.mark.asyncio
    async def test_event_loop_not_stalled_during_connect(self, fake_client, monkeypatch):
        shim = _shim(fake_client)
        _patch_loop(monkeypatch)

        ticks = []

        async def ticker():
            for _ in range(5):
                ticks.append(1)
                await asyncio.sleep(0.001)

        connect_task = asyncio.create_task(shim.connect())
        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0.01)  # connect hangs (no CONNACK yet)
        await ticker_task
        assert len(ticks) == 5  # the event loop was never stalled

        fake_client.emit_connect()
        assert await connect_task is True

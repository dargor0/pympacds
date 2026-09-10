"""MQTT shim — asyncio adapter over paho-mqtt (REQ-MQTT-SHIM-001..008).

The shim is the only component that imports ``paho.mqtt``.  It adapts paho's
blocking client to the framework's single-asyncio-loop model using paho's
"external event loop" mode: the shim registers paho's ``loop_read``/``loop_write``
on the socket via ``loop.add_reader``/``add_writer``, drives ``loop_misc`` from a
periodic task, and bridges ``on_message`` into a bounded ``asyncio.Queue``.
"""

import asyncio
import logging
import socket
from datetime import datetime
from typing import Any

try:
    import paho.mqtt.client as mqtt

    _CALLBACK_API = mqtt.CallbackAPIVersion.VERSION2
except ImportError:  # paho-mqtt is a declared dependency of the service
    mqtt = None  # type: ignore[assignment]
    _CALLBACK_API = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class MqttShim:
    """Asyncio wrapper around a paho-mqtt client (REQ-MQTT-SHIM-001).

    Args:
        config: The ``[mqtt]`` INI section (dict-like).  Values are read at
            build/connect time so that a runtime ``reconnect()`` picks up
            staged changes (REQ-MQTT-009).
        logger: Logger for operational messages.
        client_factory: Optional callable returning a paho-like client, used
            by tests to inject a fake.  Defaults to building a real paho
            client (REQ-MQTT-SHIM-001).
    """

    def __init__(self, config, logger: logging.Logger, client_factory=None):
        self._config = config
        self.logger = logger
        self._client_factory = client_factory
        self._client: Any = None

        self._connected = asyncio.Event()
        self._connect_result: asyncio.Future | None = None
        self._loop: Any = None

        queue_size = self._cfg_int("queue_size", 1000)
        if queue_size == 0:
            self.logger.warning("mqtt: queue_size=0 (unbounded) is enabled")
        self._messages: asyncio.Queue = asyncio.Queue(maxsize=queue_size)

        self._subscribed: set[str] = set()
        self._reconnect_attempts = 0
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown = False
        self._graceful = False
        self._peer = (None, None)
        self._connected_time: str | None = None
        self._disconnected_time: str | None = None

    # -- config helpers -------------------------------------------------

    def _cfg(self, key, default):
        try:
            return self._config.get(key, default)
        except AttributeError:
            return default

    def _cfg_int(self, key, default):
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key, default):
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key, default):
        return str(self._cfg(key, default)).lower() in ("true", "1", "yes", "on")

    # -- client construction -------------------------------------------

    def _build_client(self):
        """Create and configure a paho client from the current config."""
        if self._client_factory is not None:
            client = self._client_factory()
        elif mqtt is None:
            raise RuntimeError("paho-mqtt is not installed")
        else:
            client_id = self._cfg("client_id", "") or None
            version = self._cfg("mqtt_version", "3.1.1")
            protocol = mqtt.MQTTv5 if version == "5.0" else mqtt.MQTTv311
            client = mqtt.Client(
                callback_api_version=_CALLBACK_API,
                client_id=client_id or "",
                protocol=protocol,
            )

        username = self._cfg("username", "")
        password = self._cfg("password", "")
        if username:
            client.username_pw_set(username, password)

        if self._cfg_bool("use_tls", False):
            self._configure_tls(client)

        lwt_topic = self._cfg("lwt_topic", "")
        if lwt_topic:
            client.will_set(lwt_topic, self._cfg("lwt_payload", ""))

        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_socket_open = self._on_socket_open
        client.on_socket_close = self._on_socket_close
        client.on_socket_register_write = self._on_socket_register_write
        client.on_socket_unregister_write = self._on_socket_unregister_write

        return client

    def _configure_tls(self, client) -> None:
        """Configure TLS (REQ-MQTT-SHIM-007)."""
        ctx = self.build_ssl_context()
        if ctx is not None:
            client.tls_set_context(ctx)
        else:
            client.tls_set(
                ca_certs=self._cfg("tls_ca", "") or None,
                certfile=self._cfg("tls_certfile", "") or None,
                keyfile=self._cfg("tls_keyfile", "") or None,
            )
        if self._cfg_bool("tls_insecure", False):
            self.logger.warning("mqtt: tls_insecure=true (certificate verification disabled)")
            client.tls_insecure_set(True)

    def build_ssl_context(self):
        """Return a custom SSLContext, or None to use the standard tls_set path."""
        return None

    def _ensure_client(self):
        if self._client is None:
            self._client = self._build_client()

    # -- socket integration (REQ-MQTT-SHIM-003) ------------------------

    def _on_socket_open(self, client, userdata, sock):
        loop = self._loop or asyncio.get_running_loop()
        self._loop = loop
        loop.add_reader(sock.fileno(), client.loop_read)
        if client.want_write():
            loop.add_writer(sock.fileno(), client.loop_write)
        try:
            self._peer = sock.getpeername()[:2]
        except OSError:
            self._peer = (None, None)

    def _on_socket_close(self, client, userdata, sock):
        loop = self._loop or asyncio.get_running_loop()
        fd = sock.fileno()
        loop.remove_reader(fd)
        if client.want_write():
            loop.remove_writer(fd)

    def _on_socket_register_write(self, client, userdata, sock):
        loop = self._loop or asyncio.get_running_loop()
        loop.add_writer(sock.fileno(), client.loop_write)

    def _on_socket_unregister_write(self, client, userdata, sock):
        loop = self._loop or asyncio.get_running_loop()
        loop.remove_writer(sock.fileno())

    # -- paho callbacks ------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code is not None and getattr(reason_code, "is_failure", False):
            self.logger.warning("mqtt: connection rejected: %s", reason_code)
            self._resolve_connect(False)
            return
        self.logger.info("mqtt: connected")
        self._reconnect_attempts = 0
        self._connected_time = _now()
        self._disconnected_time = None
        self._connected.set()
        self._resolve_connect(True)

    def _on_connect_fail(self, client, userdata):
        self.logger.warning("mqtt: connection failed")
        self._resolve_connect(False)

    def _on_disconnect(
        self, client, userdata, disconnect_flags=None, reason_code=None, properties=None
    ):
        self.logger.warning("mqtt: disconnected")
        self._connected.clear()
        self._disconnected_time = _now()
        self._resolve_connect(False)
        if not self._graceful and not self._shutdown:
            self._schedule_reconnect()

    def _on_message(self, client, userdata, message):
        topic = message.topic
        payload = message.payload
        try:
            self._messages.put_nowait((topic, payload))
        except asyncio.QueueFull:
            self.logger.warning("mqtt: message queue overflow; dropping oldest")
            try:
                self._messages.get_nowait()
                self._messages.put_nowait((topic, payload))
            except asyncio.QueueFull:
                pass

    def _resolve_connect(self, result: bool) -> None:
        if self._connect_result is not None and not self._connect_result.done():
            self._connect_result.set_result(result)

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    # -- public asyncio API (REQ-MQTT-SHIM-001) ------------------------

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def connect(self) -> bool:
        """Connect (non-blocking).  Returns True on success."""
        self._ensure_client()
        self._graceful = False
        self._shutdown = False

        loop = asyncio.get_running_loop()
        self._loop = loop
        self._connect_result = loop.create_future()

        host = self._cfg("host", "localhost")
        port = self._cfg_int("port", 1883)
        keepalive = self._cfg_int("keepalive", 60)

        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not infos:
                raise OSError("no address resolved")
            ip = infos[0][4][0]
            self._client.connect_async(ip, port, keepalive=keepalive)
            result = await asyncio.wait_for(
                self._connect_result, timeout=self._cfg_int("connect_timeout_s", 10)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("mqtt: connect error: %s", exc)
            return False

        if result:
            await self._resubscribe()
            self._connected.set()
            return True
        if not self._graceful and not self._shutdown:
            self._schedule_reconnect()
        return False

    async def disconnect(self) -> None:
        """Graceful disconnect — suppresses automatic reconnection."""
        self._graceful = True
        self._ensure_client()
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("mqtt: disconnect error: %s", exc)
        self._connected.clear()

    async def reconnect(self) -> bool:
        """Disconnect, rebuild the client from current config, and reconnect."""
        await self.disconnect()
        self._client = None
        return await self.connect()

    # -- topic / publish operations ------------------------------------

    def subscribe(self, topic: str, qos: int | None = None) -> bool:
        self._ensure_client()
        if self._client is None or not self.connected:
            return False
        if qos is None:
            qos = self._cfg_int("qos", 1)
        try:
            result, _mid = self._client.subscribe(topic, qos=qos)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("mqtt: subscribe '%s' failed: %s", topic, exc)
            return False
        if result == 0:
            self._subscribed.add(topic)
            return True
        return False

    def unsubscribe(self, topic: str) -> bool:
        self._ensure_client()
        if self._client is None or not self.connected:
            return False
        try:
            result, _mid = self._client.unsubscribe(topic)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("mqtt: unsubscribe '%s' failed: %s", topic, exc)
            return False
        if result == 0:
            self._subscribed.discard(topic)
            return True
        return False

    def publish(self, topic: str, payload, qos: int | None = None) -> bool:
        self._ensure_client()
        if self._client is None or not self.connected:
            return False
        if qos is None:
            qos = self._cfg_int("qos", 1)
        try:
            info = self._client.publish(topic, payload, qos=qos)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("mqtt: publish '%s' failed: %s", topic, exc)
            return False
        if isinstance(info, int) and info != 0:
            return False
        return True

    async def _resubscribe(self) -> None:
        topics = list(self._subscribed)
        self._subscribed.clear()
        for topic in topics:
            self.subscribe(topic)

    # -- message source (REQ-MQTT-SHIM-005) ----------------------------

    async def messages(self):
        """Async iterator over received ``(topic, payload)`` tuples."""
        while True:
            yield await self._messages.get()

    # -- loop cadence (REQ-MQTT-SHIM-004) ------------------------------

    async def run_loop(self) -> None:
        """Periodically call ``loop_misc()``.  Run as a service task."""
        interval = self._cfg_float("loop_interval_s", 1.0)
        try:
            while True:
                if self._client is not None:
                    try:
                        self._client.loop_misc()
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning("mqtt: loop_misc error: %s", exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # -- reconnection (REQ-MQTT-SHIM-006) ------------------------------

    async def _reconnect_loop(self) -> None:
        try:
            while not self._shutdown and not self._graceful:
                min_s = self._cfg_int("reconnect_min_s", 1)
                max_s = self._cfg_int("reconnect_max_s", 60)
                delay = min(max_s, min_s * (2**self._reconnect_attempts))
                self._reconnect_attempts += 1
                self.logger.info(
                    "mqtt: reconnecting in %ss (attempt %d)", delay, self._reconnect_attempts
                )
                await asyncio.sleep(delay)
                if await self.connect():
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._reconnect_task = None

    async def stop(self) -> None:
        """Shutdown: publish LWT, unsubscribe, and disconnect (REQ-MQTT-007)."""
        self._shutdown = True
        self._graceful = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self.connected and self._client is not None:
            lwt_topic = self._cfg("lwt_topic", "")
            if lwt_topic:
                try:
                    self._client.publish(lwt_topic, self._cfg("lwt_payload", ""))
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("mqtt: LWT publish failed: %s", exc)
            for topic in list(self._subscribed):
                self.unsubscribe(topic)
        await self.disconnect()

    # -- status (REQ-MQTT-004) -----------------------------------------

    def status(self) -> dict:
        return {
            "client_id": self._cfg("client_id", ""),
            "connected": self.connected,
            "broker_ip": self._peer[0] if self._peer else None,
            "broker_port": self._peer[1] if self._peer else None,
            "protocol_version": self._cfg("mqtt_version", "3.1.1"),
            "tls": self._cfg_bool("use_tls", False),
            "subscribed_topics": sorted(self._subscribed),
            "reconnect_attempts": self._reconnect_attempts,
            "connected_time": self._connected_time,
            "disconnected_time": self._disconnected_time,
        }

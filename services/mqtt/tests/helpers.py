"""Shared test helpers for pympacds-mqtt (REQ-MQTT-008)."""


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeSocket:
    def __init__(self, fd=42, peer=("192.0.2.10", 1883)):
        self._fd = fd
        self._peer = peer

    def fileno(self):
        return self._fd

    def getpeername(self):
        return self._peer


class FakeClient:
    """A paho-like client recording calls and exposing callbacks."""

    def __init__(self):
        self.calls = []
        self.sock = FakeSocket()
        self._want_write = False
        self.on_connect = None
        self.on_connect_fail = None
        self.on_disconnect = None
        self.on_message = None
        self.on_socket_open = None
        self.on_socket_close = None
        self.on_socket_register_write = None
        self.on_socket_unregister_write = None

    # paho API used by the shim
    def connect_async(self, host, port=1883, keepalive=60, *args, **kwargs):
        self.calls.append(("connect_async", host, port, keepalive))
        if self.on_socket_open:
            self.on_socket_open(self, None, self.sock)

    def loop_read(self):
        self.calls.append(("loop_read",))

    def loop_write(self):
        self.calls.append(("loop_write",))

    def loop_misc(self):
        self.calls.append(("loop_misc",))

    def want_write(self):
        return self._want_write

    def subscribe(self, topic, qos=0):
        self.calls.append(("subscribe", topic, qos))
        return (0, 1)

    def unsubscribe(self, topic):
        self.calls.append(("unsubscribe", topic))
        return (0, 1)

    def publish(self, topic, payload=None, qos=0, *args, **kwargs):
        self.calls.append(("publish", topic, payload, qos))
        return 0

    def disconnect(self):
        self.calls.append(("disconnect",))

    def username_pw_set(self, username, password=None):
        self.calls.append(("username_pw_set", username, password))

    def tls_set(self, **kwargs):
        self.calls.append(("tls_set", kwargs))

    def tls_set_context(self, context):
        self.calls.append(("tls_set_context", context))

    def tls_insecure_set(self, value):
        self.calls.append(("tls_insecure_set", value))

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.calls.append(("will_set", topic, payload, qos))

    # test helpers
    def emit_connect(self, reason_code=None):
        if self.on_connect:
            self.on_connect(self, None, {}, reason_code)

    def emit_connect_fail(self):
        if self.on_connect_fail:
            self.on_connect_fail(self, None)

    def emit_disconnect(self):
        if self.on_disconnect:
            self.on_disconnect(self, None, None, None)

    def emit_message(self, topic, payload):
        if self.on_message:
            self.on_message(self, None, FakeMessage(topic, payload))


class FakeLoop:
    def __init__(self):
        self.readers = {}
        self.writers = {}

    def add_reader(self, fd, cb):
        self.readers[fd] = cb

    def remove_reader(self, fd):
        self.readers.pop(fd, None)

    def add_writer(self, fd, cb):
        self.writers[fd] = cb

    def remove_writer(self, fd):
        self.writers.pop(fd, None)


def make_config(**overrides):
    defaults = {
        "host": "localhost",
        "port": "1883",
        "client_id": "",
        "keepalive": "60",
        "mqtt_version": "3.1.1",
        "use_tls": "false",
        "qos": "1",
        "connect_timeout_s": "10",
        "loop_interval_s": "1.0",
        "reconnect_min_s": "1",
        "reconnect_max_s": "60",
        "subscribe_topics": "",
        "default_topic": "",
        "queue_size": "1000",
        "lwt_topic": "",
        "lwt_payload": "",
    }
    defaults.update(overrides)
    return defaults

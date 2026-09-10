"""Contract unit tests (REQ-HTTP-004)."""

import pytest

from pympacds_http.contracts import HttpContract


class FakeBase:
    def __init__(self):
        self.calls = []

    def dbus_http_send(self, payload):
        self.calls.append(("send", payload))
        return "token-send"

    def dbus_http_download(self, payload, download_path):
        self.calls.append(("download", payload, download_path))
        return "token-download"

    def dbus_http_request(self, method, url, headers, body, download_path):
        self.calls.append(("request", method, url, headers, body, download_path))
        return "token-request"


@pytest.fixture
def contract():
    base = FakeBase()
    return HttpContract("org.pympacds.HTTP", base), base


def test_contract_construction_requires_callbacks():
    class EmptyBase:
        pass

    with pytest.raises(AttributeError):
        HttpContract("org.pympacds.HTTP", EmptyBase())


def test_send_delegates(contract):
    c, base = contract
    c.send("payload")
    assert base.calls == [("send", "payload")]


def test_download_delegates(contract):
    c, base = contract
    c.download("payload", "/tmp/x.bin")
    assert base.calls == [("download", "payload", "/tmp/x.bin")]


def test_request_delegates(contract):
    c, base = contract
    c.request("GET", "http://x", "{}", "body", "")
    assert base.calls == [("request", "GET", "http://x", "{}", "body", "")]


def test_request_completed_signal_returns_args(contract):
    c, _ = contract
    assert c.request_completed("tok", "{}") == ["tok", "{}"]


def test_contract_metadata():
    assert HttpContract.iface_name == "HTTP"
    assert "http" in HttpContract.iface_provides
    assert "network" in HttpContract.iface_requires

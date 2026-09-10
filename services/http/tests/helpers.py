"""Shared test helpers for pympacds-http (REQ-HTTP-007)."""


class FakeResponse:
    """An aiohttp.ClientResponse stand-in."""

    def __init__(self, status=200, body=b"ok", url="http://example.test/"):
        self.status = status
        self.url = url
        self._body = body

    async def read(self):
        return self._body


class FailingResponse:
    """A response whose body read raises a transport error."""

    status = None
    url = "http://example.test/"

    async def read(self):
        raise OSError("connection reset")


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """An aiohttp.ClientSession stand-in recording requests."""

    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses) if responses else []
        self.closed = False
        self._idx = 0

    def request(self, method, url, headers=None, data=None, max_redirects=10):
        self.requests.append((method, url, headers, data, max_redirects))
        if self._idx < len(self.responses):
            resp = self.responses[self._idx]
        else:
            resp = FakeResponse()
        self._idx += 1
        return _AsyncContextManager(resp)

    async def close(self):
        self.closed = True


def make_config(**overrides):
    defaults = {
        "timeout_s": "10",
        "retries": "3",
        "retry_backoff_s": "1",
        "tls_verify": "true",
        "tls_certfile": "",
        "tls_keyfile": "",
        "max_redirects": "5",
        "default_url": "http://example.test/",
        "default_method": "POST",
        "default_headers": "",
    }
    defaults.update(overrides)
    return defaults

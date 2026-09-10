"""HTTP D-Bus contract (REQ-HTTP-004)."""

from pympacds.contracts import ServiceContract, dbus_method, dbus_signal


class HttpContract(ServiceContract):
    """HTTP request interface."""

    iface_name = "HTTP"
    iface_version = "1.0.0"
    iface_provides = ["http"]
    iface_requires = ["network"]

    def __init__(self, ifname: str, base):
        super().__init__(ifname, base)
        self._require(
            "dbus_http_send",
            "dbus_http_download",
            "dbus_http_request",
        )

    @dbus_method()
    def send(self, payload: "s") -> "s":
        return self.base.dbus_http_send(payload)

    @dbus_method()
    def download(self, payload: "s", download_path: "s") -> "s":
        return self.base.dbus_http_download(payload, download_path)

    @dbus_method()
    def request(
        self,
        method: "s",
        url: "s",
        headers: "s",
        body: "s",
        download_path: "s",
    ) -> "s":
        return self.base.dbus_http_request(method, url, headers, body, download_path)

    @dbus_signal()
    def request_completed(self, token: "s", result: "s") -> "ss":
        return [token, result]

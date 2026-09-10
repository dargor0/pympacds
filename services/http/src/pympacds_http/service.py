"""HTTP client service (REQ-HTTP-001..007)."""

import asyncio
import configparser
import json
import os
import secrets
import ssl
import tempfile
import time
from typing import Any

import aiohttp
from pympacds.builtin_contracts import ConfigContract, HealthContract
from pympacds.dbus import DBusManager
from pympacds.process import ProcessBase

from .contracts import HttpContract


class HttpService(ProcessBase):
    """Sends HTTP requests on behalf of other services, exposed over D-Bus.

    ``send``/``download``/``request`` follow a fire-and-forget pattern: each
    returns a random request token immediately and the actual request runs as
    an asyncio task; on completion the ``request_completed(token, result)``
    signal is emitted (REQ-HTTP-004).

    Args:
        session_factory: Optional callable ``f(service) -> session`` returning
            an aiohttp-like session, used by tests to inject a fake (no
            network).  Defaults to building a real ``aiohttp.ClientSession``.
    """

    def __init__(self, session_factory=None):
        super().__init__(
            name="http",
            version="0.1.0",
            description="HTTP client service for pympacds",
        )
        self.bus: Any = None  # DBusManager, created in start_dbus()
        self._start_time = time.time()
        self._session_factory = session_factory
        self._session: aiohttp.ClientSession | None = None
        self._contract = None
        self._health_contract = None
        self._config_contract = None
        self._tasks: set[asyncio.Task] = set()

    # -- config helpers -------------------------------------------------

    def _cfg(self, key: str, default):
        try:
            return self.config["http"].get(key, default)
        except (KeyError, configparser.Error):
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return str(self._cfg(key, default)).lower() in ("true", "1", "yes", "on")

    def _dbus_flag(self, key: str, default: bool) -> bool:
        if not self.config.has_section("dbus"):
            return default
        try:
            return self.config["dbus"].getboolean(key, default)
        except ValueError:
            return default

    # -- synchronous setup ----------------------------------------------

    def setup(self, inputargs: list[str] | None = None) -> bool:
        """Parse args, load config, and validate the mutual-TLS pair."""
        if not super().setup(inputargs):
            return False
        return self._validate_tls_pair()

    def _validate_tls_pair(self) -> bool:
        """Require ``tls_certfile``/``tls_keyfile`` together (REQ-HTTP-006)."""
        certfile = self._cfg("tls_certfile", "").strip()
        keyfile = self._cfg("tls_keyfile", "").strip()
        if bool(certfile) != bool(keyfile):
            self.logger.error("http.tls_certfile and http.tls_keyfile must be provided together")
            return False
        return True

    # -- D-Bus setup ----------------------------------------------------

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="http",
            bus_prefix=self.bus_prefix,
        )

        self._contract = HttpContract(f"{self.bus_prefix}.HTTP", self)
        self.bus.add_interface("http", self._contract)

        if self._dbus_flag("contract_health", True):
            self._health_contract = HealthContract(f"{self.bus_prefix}.Health", self)
            self.bus.add_interface("health", self._health_contract)

        if self._dbus_flag("contract_config", False):
            self._config_contract = ConfigContract(f"{self.bus_prefix}.Config", self)
            self.bus.add_interface("config", self._config_contract)

        await self.bus.start()

        self._build_session()
        return True

    def _build_session(self) -> None:
        """Build the aiohttp session (no network I/O) — REQ-HTTP-006."""
        if self._session_factory is not None:
            self._session = self._session_factory(self)
            return
        timeout = aiohttp.ClientTimeout(total=self._cfg_int("timeout_s", 10))
        connector = aiohttp.TCPConnector(ssl=self._build_ssl_context())
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    def _build_ssl_context(self):
        """Return the connector ``ssl`` value: a context for mutual TLS,
        ``False`` to disable verification, or ``True`` for the default."""
        tls_verify = self._cfg_bool("tls_verify", True)
        certfile = self._cfg("tls_certfile", "").strip()
        keyfile = self._cfg("tls_keyfile", "").strip()

        if certfile and keyfile:
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
            if not tls_verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if not tls_verify:
            return False
        return True

    # -- fire-and-forget D-Bus handlers (REQ-HTTP-004) ------------------

    def dbus_http_send(self, payload: str) -> str:
        token = secrets.token_hex(16)
        self._spawn(token, self._run_send(payload))
        return token

    def dbus_http_download(self, payload: str, download_path: str) -> str:
        token = secrets.token_hex(16)
        self._spawn(token, self._run_send(payload, download_path=download_path))
        return token

    def dbus_http_request(
        self,
        method: str,
        url: str,
        headers: str,
        body: str,
        download_path: str,
    ) -> str:
        token = secrets.token_hex(16)
        self._spawn(token, self._run_request(method, url, headers, body, download_path))
        return token

    def _spawn(self, token: str, coro) -> None:
        """Schedule a request task on the running loop."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._complete(token, coro), name=f"http_{token}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _complete(self, token: str, coro) -> None:
        """Await a request and emit ``request_completed`` (never lost)."""
        try:
            result = await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            result = {
                "status_code": None,
                "url": "",
                "elapsed_ms": 0,
                "body": None,
                "download_path": None,
                "bytes_written": None,
                "error": f"internal error: {exc}",
            }
        self._emit_completed(token, result)

    def _emit_completed(self, token: str, result: dict) -> None:
        if self._contract is not None:
            self._contract.request_completed(token, json.dumps(result))

    # -- request execution ----------------------------------------------

    async def _run_send(self, payload: str, download_path: str | None = None) -> dict:
        url = self._cfg("default_url", "").strip()
        method = self._cfg("default_method", "POST").strip()
        headers = self._parse_headers(self._cfg("default_headers", ""))
        return await self._execute(
            method, url, headers, payload, self._normalize_path(download_path)
        )

    async def _run_request(
        self,
        method: str,
        url: str,
        headers: str,
        body: str,
        download_path: str,
    ) -> dict:
        parsed_headers = self._parse_headers(headers)
        return await self._execute(
            method, url, parsed_headers, body, self._normalize_path(download_path)
        )

    @staticmethod
    def _normalize_path(path: str | None) -> str | None:
        if not path:
            return None
        path = path.strip()
        return path or None

    def _parse_headers(self, raw: str) -> dict:
        """Parse a JSON-object header string; invalid input yields {}."""
        if not raw or not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.warning("http: headers are not valid JSON; ignoring")
            return {}
        if not isinstance(data, dict):
            self.logger.warning("http: headers must be a JSON object; ignoring")
            return {}
        return {str(k): str(v) for k, v in data.items()}

    async def _execute(
        self,
        method: str,
        url: str,
        headers: dict,
        body: str,
        download_path: str | None,
    ) -> dict:
        """Run a request with retry/backoff and return the result JSON (REQ-HTTP-005)."""
        start = time.monotonic()

        def _result(**fields) -> dict:
            result = {
                "status_code": None,
                "url": url,
                "elapsed_ms": self._elapsed_ms(start),
                "body": None,
                "download_path": download_path,
                "bytes_written": None,
                "error": None,
            }
            result.update(fields)
            return result

        if not url:
            return _result(error="no URL configured")

        session = self._session
        if session is None:
            return _result(error="HTTP session is not started")

        retries = max(0, self._cfg_int("retries", 3))
        backoff = max(0, self._cfg_int("retry_backoff_s", 1))

        for attempt in range(1, retries + 2):
            ok, fields = await self._attempt(session, method, url, headers, body, download_path)
            if ok:
                return _result(**fields)
            if attempt <= retries:
                delay = backoff * (2 ** (attempt - 1))
                self.logger.warning(
                    "http: attempt %d failed (%s); retrying in %.1fs",
                    attempt,
                    fields.get("error"),
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                return _result(**fields)

        return _result(error="unreachable")  # pragma: no cover

    async def _attempt(
        self,
        session,
        method: str,
        url: str,
        headers: dict,
        body: str,
        download_path: str | None,
    ) -> tuple[bool, dict]:
        """Perform one request.  Returns ``(ok, fields)``; ``ok=False`` marks
        a transient failure (network error, timeout, or 5xx) to retry."""
        max_redirects = max(0, self._cfg_int("max_redirects", 5))
        try:
            async with session.request(
                method,
                url,
                headers=headers or None,
                data=body or None,
                max_redirects=max_redirects,
            ) as resp:
                status = resp.status
                final_url = str(resp.url)
                payload = await resp.read()

                if status >= 500:
                    return False, {
                        "status_code": status,
                        "url": final_url,
                        "error": f"server returned HTTP {status}",
                    }

                fields: dict = {"status_code": status, "url": final_url, "error": None}
                if download_path:
                    try:
                        written = self._write_atomic(download_path, payload)
                    except OSError as exc:
                        fields["error"] = f"download write failed: {exc}"
                        return True, fields
                    fields["download_path"] = download_path
                    fields["bytes_written"] = written
                else:
                    fields["body"] = payload.decode("utf-8", errors="replace")
                return True, fields
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
            return False, {"error": str(exc)}

    @staticmethod
    def _write_atomic(path: str, data: bytes) -> int:
        """Write ``data`` to ``path`` atomically (temp file + rename)."""
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory or ".", prefix=".httpdl-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return len(data)

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)

    # -- graceful shutdown (REQ-HTTP-006) -------------------------------

    async def close_loop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None
        await super().close_loop()

    # -- health / config contract base callbacks (REQ-XCUT-004) --------

    def dbus_health_ping(self) -> bool:
        return True

    def dbus_health_status(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "uptime": self.dbus_health_get_uptime(),
                "provides": self.dbus_health_get_provides(),
                "requires": self.dbus_health_get_requires(),
            }
        )

    def dbus_health_get_uptime(self) -> int:
        return int(time.time() - self._start_time)

    def dbus_config_get(self, section: str, key: str) -> str:
        try:
            return self.config[section][key]
        except (KeyError, configparser.Error):
            return ""

    def dbus_config_set(self, section: str, key: str, value: str) -> bool:
        try:
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config[section][key] = value
        except Exception:  # noqa: BLE001
            return False
        if self._config_contract is not None:
            try:
                self._config_contract.config_changed(section, key, value)
            except Exception:  # noqa: BLE001
                pass
        return True


def main() -> None:
    HttpService().start()


if __name__ == "__main__":
    main()

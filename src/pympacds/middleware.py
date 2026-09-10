"""Middleware — optional framework extensions that hook into the service
lifecycle.
"""

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass

from .process import ProcessBase

_logger = logging.getLogger(__name__)


@dataclass
class MiddlewareSpec:
    """Describes a middleware to activate programmatically (REQ-SVC-015).

    Attributes:
        middleware: A ``MiddlewareBase`` subclass, or an entry-point name
            (string) registered under the ``pympacds.middleware`` group.
        section: The INI section name for the middleware's configuration,
            or ``None`` if no dedicated section is used.
    """

    middleware: type | str
    section: str | None = None


class MiddlewareBase:
    """Base class for pympacds middleware.

    Middleware hooks into the service lifecycle to add background tasks,
    integrate external systems, or modify startup behaviour.

    Args:
        service: The ``ProcessBase`` instance.
        section: The INI section name for this middleware's configuration,
            or ``None`` if no dedicated section is used.
    """

    def __init__(self, service: ProcessBase, section: str | None):
        self.service = service
        self.section = section
        self.logger = service.logger.getChild(self.__class__.__name__.lower())
        if section is not None and service.config.has_section(section):
            self._config = service.config[section]
        else:
            self._config = {}

    @property
    def config(self) -> dict:
        """The middleware's configuration section (a dict-like object)."""
        return self._config

    async def setup(self) -> None:
        """Called during ``init_loop()``, after ``start_dbus()`` succeeds.

        Override to add tasks to ``service.tasklist``, create D-Bus
        proxies, or perform one-shot I/O.
        """

    async def teardown(self) -> None:
        """Called during ``close_loop()``, before ``bus.stop()``.

        Override to clean up non-task resources (external connections,
        temporary files, buffers).
        """

    def on_error(self, error: Exception) -> bool:
        """Called when ``setup()`` or ``teardown()`` raises.

        Return ``True`` to continue despite the error; ``False`` to abort
        (only meaningful during ``setup()`` — teardown always proceeds).
        """
        self.logger.error("Middleware error: %s", error)
        return False


# ------------------------------------------------------------------
# built-in: httpconfprov
# ------------------------------------------------------------------


class HttpConfigMiddleware(MiddlewareBase):
    """HTTP config downloader middleware.

    Activated via::

        [middleware]
        httpconfprov = my_http_section

        [my_http_section]
        url = https://provision.example.com/device/%s
        timeout_s = 10
        tls_verify = true
    """

    async def setup(self) -> None:
        url = self._config.get("url", "").strip()
        if not url:
            self.logger.warning("httpconfprov: no 'url' configured, skipping")
            return

        # substitute %s with service name
        url = url.replace("%s", self.service.name)

        timeout = int(self._config.get("timeout_s", 10))
        tls_verify = self._config.get("tls_verify", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        try:
            # use synchronous urllib in a thread to avoid blocking
            # the event loop
            loop = asyncio.get_running_loop()

            def _fetch():
                ctx = None
                if not tls_verify:
                    import ssl

                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url)
                return urllib.request.urlopen(req, timeout=timeout, context=ctx)

            response = await loop.run_in_executor(None, _fetch)
            data = response.read().decode("utf-8")
            response.close()
            remote = json.loads(data)
        except Exception as exc:
            self.logger.warning("httpconfprov: fetch failed: %s", exc)
            # continue despite failure
            return

        # compare with current config
        current_json = json.dumps(
            {s: dict(self.service.config[s]) for s in self.service.config.sections()},
            sort_keys=True,
        )
        remote_json = json.dumps(remote, sort_keys=True)

        if current_json == remote_json:
            self.logger.debug("httpconfprov: config unchanged")
            return

        # write new config
        import configparser

        cp = configparser.ConfigParser()
        for section, items in remote.items():
            cp[section] = items
        with open(self.service.args.configfile, "w") as f:
            cp.write(f)

        self.logger.info("httpconfprov: config updated from %s, restarting", url)
        self.service.exitevent.set()

"""ProcessBase — the foundation class for all pympacds services."""

import sys
import os
import signal
import logging
import logging.handlers
import configparser
import argparse
import asyncio

from .dbus import DBusManager

class ProcessBase:
    """Base class for all pympacds services.

    Every service must subclass ProcessBase, override ``start_dbus()``,
    and optionally override ``update_tasks()`` and ``setup()``.

    Lifecycle:
        setup() → start() → asyncio.run(main_loop())
                          → init_loop() → main loop → close_loop()
    """

    def __init__(
        self,
        name: str,
        version: str,
        description: str = "",
        bus_prefix: str = "org.pympacds",
    ):
        self.name = name
        self.version = version
        self.pid = os.getpid()
        self._bus_prefix = bus_prefix

        self.config = configparser.ConfigParser()
        self.argparser = argparse.ArgumentParser(description=description)
        self.argparser.add_argument(
            "-c", "--configfile", help="Main configuration file"
        )
        
        self.bus = None

        self.exitevent: asyncio.Event | None = None
        self.tasklist: dict[str, asyncio.Task] = {}
        self._middleware_instances: list = []
        self._validate_user_schema: bool = True
        self._schema_file: str | None = None

        self.logger = logging.getLogger(f"PID={self.pid} pympacds.{name}")

    @property
    def bus_prefix(self) -> str:
        return self._bus_prefix

    # ------------------------------------------------------------------
    # synchronous setup
    # ------------------------------------------------------------------

    def setup(self, inputargs: list[str] | None = None) -> bool:
        """Parse CLI args, load INI config, configure logging, validate.

        Must be called before ``main_loop()``.  Subclasses may override
        to add their own synchronous initialisation — the override must
        call ``super().setup(inputargs)`` first.

        Returns:
            True on success, False if validation failed.
        """
        if inputargs is None:
            inputargs = sys.argv[1:]
        self.args = self.argparser.parse_args(inputargs)

        if not self.args.configfile:
            self.argparser.error("the following argument is required: -c/--configfile")

        # load configuration
        self.config.read(self.args.configfile)

        # apply [dbus] INI overrides (bus_prefix, etc.)
        self._apply_dbus_config()

        # validate configuration BEFORE setting up logging
        if not self._validate_config():
            return False

        # configure logging
        self._setup_logging()

        self.logger.debug(
            "Start process %s %s (config file %s)",
            self.name,
            self.version,
            self.args.configfile,
        )

        return True

    def _apply_dbus_config(self) -> None:
        """Apply [dbus] section overrides from the INI file."""
        if not self.config.has_section("dbus"):
            return
        dbus_cfg = self.config["dbus"]
        self._bus_prefix = dbus_cfg.get("bus_prefix", self._bus_prefix)
        self._validate_user_schema = dbus_cfg.getboolean(
            "validate_user_schema", True
        )
        if self._validate_user_schema:
            self._schema_file = dbus_cfg.get("schema_file", None) or None

    def _setup_logging(self) -> None:
        """Configure log level, stdout handler, and rotating file handler."""
        # clear any handlers from previous setup() calls on this logger
        self.logger.handlers.clear()

        try:
            lev = getattr(
                logging,
                self.config["DEFAULT"].get("loglevel", "WARN").upper(),
            )
        except (KeyError, AttributeError):
            lev = logging.WARN
        self.logger.setLevel(lev)

        # asyncio-aware filter
        class AsyncioFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                try:
                    ct = asyncio.current_task()
                except RuntimeError:
                    record.taskid = "(NoLoop)"
                else:
                    record.taskid = ct.get_name() if ct is not None else "(NoTask)"
                return True

        af = AsyncioFilter()

        logformat = self.config["DEFAULT"].get(
            "logformat",
            "%(asctime)s - %(levelname)-8s - %(taskid)-10s - %(name)s: %(message)s",
        )
        logdate = self.config["DEFAULT"].get("logdate", "%Y-%m-%dT%H:%M:%S")
        ft = logging.Formatter(fmt=logformat, datefmt=logdate)

        if self.config["DEFAULT"].getboolean("logstdout", False):
            ch = logging.StreamHandler()
            ch.addFilter(af)
            ch.setLevel(lev)
            ch.setFormatter(ft)
            self.logger.addHandler(ch)

        logfile = self.config["DEFAULT"].get("logfile", "")
        if logfile:
            logsuffix = self.config["DEFAULT"].get("logfilesuffix", "")
            if logsuffix:
                logf = os.path.splitext(logfile)
                logfile = (
                    logf[0] + "_" + getattr(self.args, logsuffix, "") + logf[1]
                )
            logbasedir = os.path.dirname(logfile)
            if not os.path.isdir(logbasedir):
                try:
                    os.makedirs(logbasedir, exist_ok=True)
                except OSError:
                    pass
            try:
                lf = logging.handlers.RotatingFileHandler(
                    logfile,
                    maxBytes=self.config["DEFAULT"].getint("logmaxsize", 2**22),
                    backupCount=self.config["DEFAULT"].getint("logmaxcount", 10),
                )
            except OSError:
                logfile = os.path.join("/tmp", os.path.basename(logfile))
                lf = logging.handlers.RotatingFileHandler(
                    logfile,
                    maxBytes=self.config["DEFAULT"].getint("logmaxsize", 2**22),
                    backupCount=self.config["DEFAULT"].getint("logmaxcount", 10),
                )
            lf.addFilter(af)
            lf.setLevel(lev)
            lf.setFormatter(ft)
            self.logger.addHandler(lf)

    # ------------------------------------------------------------------
    # built-in configuration validation
    # ------------------------------------------------------------------

    def _validate_config(self) -> bool:
        """Validate built-in [DEFAULT] and [dbus] keys.  Optionally validate
        user sections against a JSON schema.

        Returns:
            True if all validations pass or no schema is present.
        """
        errors: list[str] = []
        self._validate_default_section(errors)
        self._validate_dbus_section(errors)
        if self._validate_user_schema and self._schema_file:
            self._validate_user_schema_file(errors)

        if errors:
            for e in errors:
                self.logger.error(e)
            return False
        return True

    def _validate_default_section(self, errors: list[str]) -> None:
        """Validate keys in the [DEFAULT] section."""
        d = self.config["DEFAULT"]
        # loglevel
        lvl = d.get("loglevel", "WARN").upper()
        if lvl not in {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}:
            errors.append(f"DEFAULT.loglevel: invalid level '{lvl}'")
        # logstdout
        try:
            d.getboolean("logstdout", False)
        except ValueError:
            errors.append("DEFAULT.logstdout: must be true/false")
        # logmaxsize
        try:
            ms = d.getint("logmaxsize", 2**22)
            if ms <= 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("DEFAULT.logmaxsize: must be a positive integer")
        # logmaxcount
        try:
            mc = d.getint("logmaxcount", 10)
            if mc < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("DEFAULT.logmaxcount: must be a non-negative integer")

    def _validate_dbus_section(self, errors: list[str]) -> None:
        """Validate keys in the [dbus] section."""
        d = self.config["dbus"] if self.config.has_section("dbus") else {}
        bt = d.get("bus_type", "system")
        if bt not in {"system", "session"}:
            errors.append(f"dbus.bus_type: must be 'system' or 'session', got '{bt}'")
        try:
            dto = d.getint("drain_timeout_ms", 2000) if d else 2000
            if dto < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("dbus.drain_timeout_ms: must be a non-negative integer")
        for key in ("contract_health", "contract_metrics",
                     "contract_lifecycle", "contract_config"):
            if d and key in d:
                try:
                    d.getboolean(key)
                except ValueError:
                    errors.append(f"dbus.{key}: must be true/false")
        if d and "heartbeat_interval_s" in d:
            try:
                hb = d.getint("heartbeat_interval_s")
                if hb < 5 or hb > 3600:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(
                    "dbus.heartbeat_interval_s: must be an integer between 5 and 3600"
                )
        if d and "discovery_enabled" in d:
            try:
                d.getboolean("discovery_enabled")
            except ValueError:
                errors.append("dbus.discovery_enabled: must be true/false")

    def _validate_user_schema_file(self, errors: list[str]) -> None:
        """Validate user-defined sections against a JSON schema file."""
        import json

        try:
            with open(self._schema_file) as f:
                schema = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"schema: cannot load '{self._schema_file}': {exc}")
            return

        for section, spec in schema.items():
            if section in ("DEFAULT", "dbus"):
                continue
            if not self.config.has_section(section):
                continue
            required = spec.get("required", [])
            keys = spec.get("keys", {})
            for rk in required:
                if rk not in self.config[section]:
                    errors.append(f"{section}.{rk}: required key is missing")
            for kname, kspec in keys.items():
                if kname not in self.config[section]:
                    if "default" in kspec:
                        self.config[section][kname] = str(kspec["default"])
                    continue
                val = self.config[section][kname]
                ktype = kspec.get("type", "str")
                if ktype == "int":
                    try:
                        ival = int(val)
                        if "min" in kspec and ival < kspec["min"]:
                            errors.append(
                                f"{section}.{kname}: {ival} < min {kspec['min']}"
                            )
                        if "max" in kspec and ival > kspec["max"]:
                            errors.append(
                                f"{section}.{kname}: {ival} > max {kspec['max']}"
                            )
                    except ValueError:
                        errors.append(f"{section}.{kname}: expected int, got '{val}'")
                elif ktype == "float":
                    try:
                        fval = float(val)
                        if "min" in kspec and fval < kspec["min"]:
                            errors.append(
                                f"{section}.{kname}: {fval} < min {kspec['min']}"
                            )
                        if "max" in kspec and fval > kspec["max"]:
                            errors.append(
                                f"{section}.{kname}: {fval} > max {kspec['max']}"
                            )
                    except ValueError:
                        errors.append(
                            f"{section}.{kname}: expected float, got '{val}'"
                        )
                elif ktype == "bool":
                    try:
                        self.config.getboolean(section, kname)
                    except ValueError:
                        errors.append(
                            f"{section}.{kname}: expected bool, got '{val}'"
                        )
                elif ktype == "str" and "pattern" in kspec:
                    import re

                    if not re.match(kspec["pattern"], val):
                        errors.append(
                            f"{section}.{kname}: '{val}' does not match "
                            f"pattern '{kspec['pattern']}'"
                        )

    # ------------------------------------------------------------------
    # asyncio lifecycle
    # ------------------------------------------------------------------

    def start(self, inputargs: list[str] | None = None) -> None:
        """Convenience: run setup() then the asyncio event loop.

        When writing tests with ``pytest-asyncio``, skip ``start()``
        and invoke ``setup()`` + a controlled ``asyncio`` run directly.
        """
        if not self.setup(inputargs):
            self.logger.error("Configuration validation failed. Exiting.")
            sys.exit(1)
        asyncio.run(self.main_loop())
        
    def setup_dbus(self) -> None:
        """Initialize D-Bus resources. Here the Contract should be added."""
        self.bus = DBusManager(
            logger=self.logger,
            busname="sensor",
        )

    async def start_dbus(self) -> bool:
        """Initialize D-Bus resources (abstract — subclasses must override).

        Returns:
            True on success, False on failure.
        """
        
        raise NotImplementedError(
            "start_dbus() coroutine must be subclassed."
        )

    async def init_loop(self) -> bool:
        """Initialize the asyncio event loop.

        Returns:
            True if initialization succeeded, False otherwise.
        """
        self.exitevent = asyncio.Event()
        self._sighdl_register_term()

        # instantiate middleware (sync phase)
        self._init_middleware()

        if not await self.start_dbus():
            self.logger.error("D-Bus initialization failed")
            self.exitevent.set()
            return False

        # middleware async setup
        if not await self._setup_middleware():
            return False

        self.tasklist.clear()
        self.tasklist["WaitExit"] = asyncio.create_task(
            self.do_waitexit(), name="WaitExit"
        )
        return True

    async def main_loop(self) -> None:
        """Run the main event loop."""
        if not await self.init_loop():
            await self.close_loop()
            return

        while not self.exitevent.is_set():
            self.update_tasks()
            try:
                done, _pending = await asyncio.wait(
                    set(self.tasklist.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                exitloop = False
                for t in done:
                    tname = t.get_name()
                    if tname == "WaitExit":
                        exitloop = True
                    self.tasklist.pop(tname, None)
                if exitloop:
                    break
            except asyncio.CancelledError:
                self.logger.debug("Main loop received cancellation. Exiting.")
            except Exception:
                self.logger.exception("Main loop exception. Exiting.")
                break

        await self.close_loop()

    async def close_loop(self) -> None:
        """Gracefully cancel tasks, tear down middleware, disconnect D-Bus."""
        for tname, t in list(self.tasklist.items()):
            if not t.done():
                try:
                    await asyncio.wait_for(t, timeout=0.01)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    self.logger.exception(
                        "Wait for cancelled task %s exception", tname
                    )
                    continue
                t.cancel()
                try:
                    await asyncio.wait_for(t, timeout=1)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    self.logger.error(
                        "Wait for cancelled task %s timeout", tname
                    )
                except Exception:
                    self.logger.exception(
                        "Wait for cancelled task %s exception", tname
                    )

        # middleware teardown (reverse order)
        for mw in reversed(self._middleware_instances):
            try:
                await mw.teardown()
            except Exception as exc:
                mw.on_error(exc)
                self.logger.exception("Middleware teardown error")

        if hasattr(self, "bus") and self.bus is not None:
            await self.bus.stop()

        self.logger.debug("Exit done.")

    def update_tasks(self) -> None:
        """Hook called on every main loop iteration.

        Subclasses override this to lazily create asyncio tasks.
        """

    async def do_waitexit(
        self,
        timeout: int | None = None,
        events: list | None = None,
    ) -> None:
        """Await the exit event with optional timeout and extra awaitables.

        Args:
            timeout: Max wait in seconds, or None to wait indefinitely.
            events: Additional awaitables to wait on alongside the exit event.
        """
        try:
            if events is not None:
                local = [asyncio.ensure_future(e) for e in events]
                local.append(asyncio.ensure_future(self.exitevent.wait()))
                await asyncio.wait(
                    local, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
            elif timeout is not None and timeout > 0:
                await asyncio.wait_for(self.exitevent.wait(), timeout=timeout)
            else:
                await self.exitevent.wait()
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            if timeout is not None and timeout > 0:
                raise

    # ------------------------------------------------------------------
    # signal handling
    # ------------------------------------------------------------------

    def _sighdl_register_term(self) -> None:
        """Register SIGHUP, SIGTERM, SIGINT handlers on the running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logger.exception("Cannot get asyncio loop.")
            return
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    signum,
                    lambda s=signum: asyncio.create_task(
                        self._signal_term_handler(s)
                    ),
                )
            except (ValueError, OSError):
                self.logger.exception(
                    "Cannot register signal handler for %d", signum
                )

    async def _signal_term_handler(self, signum: int) -> None:
        """Handle a termination signal."""
        try:
            self.logger.debug("Received termination signal (%d)", signum)
            if self.exitevent:
                self.exitevent.set()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # middleware support
    # ------------------------------------------------------------------

    def _load_middleware_from_config(self) -> list[tuple[str, str | None]]:
        """Parse the [middleware] section and return a list of
        (entry_point_name, config_section_or_None) tuples.
        """
        if not self.config.has_section("middleware"):
            return []
        result: list[tuple[str, str | None]] = []
        for key, value in self.config["middleware"].items():
            section: str | None = value.strip()
            if section.lower() == "none":
                section = None
            result.append((key, section))
        return result

    def _init_middleware(self) -> None:
        """Discover and instantiate enabled middleware (sync phase)."""
        try:
            import importlib.metadata
        except ImportError:
            self.logger.warning("importlib.metadata not available; middleware skipped")
            return

        eps = importlib.metadata.entry_points(group="pympacds.middleware")
        ep_map = {ep.name: ep for ep in eps}

        for mw_name, section in self._load_middleware_from_config():
            if mw_name not in ep_map:
                self.logger.error(
                    "Middleware '%s' not found in installed entry points", mw_name
                )
                continue
            try:
                cls = ep_map[mw_name].load()
                instance = cls(self, section)
                self._middleware_instances.append(instance)
                self.logger.debug("Middleware '%s' instantiated", mw_name)
            except Exception:
                self.logger.exception(
                    "Failed to instantiate middleware '%s'", mw_name
                )

    async def _setup_middleware(self) -> bool:
        """Run async setup for all middleware instances."""
        failed: list[int] = []
        for i, mw in enumerate(self._middleware_instances):
            try:
                await mw.setup()
            except Exception as exc:
                if not mw.on_error(exc):
                    self.logger.error(
                        "Middleware setup failed (aborting): %s", exc
                    )
                    return False
                failed.append(i)
                self.logger.warning(
                    "Middleware setup error (continuing): %s", exc
                )
        # remove failed middleware from the list
        for i in reversed(failed):
            self._middleware_instances.pop(i)
        return True

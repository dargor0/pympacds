"""pympacds-admin — CLI administration tool."""

import argparse
import os
import shutil
import subprocess
import sys
import textwrap


def main() -> None:
    """Entry point for ``pympacds-admin``."""
    parser = argparse.ArgumentParser(
        prog="pympacds-admin",
        description="pympacds administration tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # setup
    sp_setup = sub.add_parser("setup", help="Install framework-wide files")
    _add_setup_args(sp_setup)

    # install
    sp_install = sub.add_parser("install", help="Install a service (systemd + D-Bus)")
    sp_install.add_argument("service_name", help="Name of the service to install")
    sp_install.add_argument("--no-enable", action="store_true", help="Skip systemctl enable")
    sp_install.add_argument("--start", action="store_true", help="Start the service after install")
    sp_install.add_argument("--dry-run", action="store_true", help="Print paths only")

    # new
    sp_new = sub.add_parser("new", help="Scaffold a new service")
    sp_new.add_argument("service_name", help="Name of the service to create")

    # new-middleware
    sp_nmw = sub.add_parser("new-middleware", help="Scaffold a new middleware package")
    sp_nmw.add_argument("name", help="Name of the middleware to create")

    # list
    sub.add_parser("list", help="List running pympacds services on the bus")

    # config
    sp_cfg = sub.add_parser("config", help="Configuration management")
    cfg_sub = sp_cfg.add_subparsers(dest="cfg_command")

    cfg_show = cfg_sub.add_parser("show", help="Display current configuration")
    _add_config_file_arg(cfg_show)

    cfg_get = cfg_sub.add_parser("get", help="Get a config value")
    cfg_get.add_argument("section", help="INI section")
    cfg_get.add_argument("key", help="Config key")
    _add_config_file_arg(cfg_get)

    cfg_set = cfg_sub.add_parser("set", help="Set a config value")
    cfg_set.add_argument("section", help="INI section")
    cfg_set.add_argument("key", help="Config key")
    cfg_set.add_argument("value", help="Config value")
    _add_config_file_arg(cfg_set)

    cfg_rm = cfg_sub.add_parser("remove", help="Remove a config key")
    cfg_rm.add_argument("section", help="INI section")
    cfg_rm.add_argument("key", help="Config key")
    _add_config_file_arg(cfg_rm)

    cfg_val = cfg_sub.add_parser("validate", help="Validate config against schema")
    _add_config_file_arg(cfg_val)
    cfg_val.add_argument("-s", "--schema", help="Schema file path (JSON)")

    args = parser.parse_args()

    if args.command == "setup":
        _cmd_setup(args)
    elif args.command == "install":
        _cmd_install(args)
    elif args.command == "new":
        _cmd_new(args)
    elif args.command == "new-middleware":
        _cmd_new_middleware(args)
    elif args.command == "list":
        _cmd_list(args)
    elif args.command == "config":
        _cmd_config(args)
    else:
        parser.print_help()


# ------------------------------------------------------------------
# setup
# ------------------------------------------------------------------


def _add_setup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bus-prefix", default="org.pympacds", help="Bus name prefix")
    parser.add_argument("--resource-dir", default="/usr/share/pympacds", help="Template directory")
    parser.add_argument("--no-dirs", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def _cmd_setup(args) -> None:
    _ensure_root()

    resource_dir = args.resource_dir
    bus_prefix = args.bus_prefix

    # 1. create system user
    if not args.no_dirs:
        _run(["id", "pympacds-user"], check=False, quiet=True)
        if _run(["id", "pympacds-user"], check=False, quiet=True).returncode != 0:
            _run(["adduser", "--system", "--no-create-home", "--group", "pympacds-user"])
            _run(["adduser", "pympacds-user", "dbus"], check=False)

    # 2. create /var/log/pympacds
    if not args.no_dirs:
        os.makedirs("/var/log/pympacds", exist_ok=True)
        _run(["chown", "pympacds-user:pympacds-user", "/var/log/pympacds"])

    # 3. install D-Bus security policy
    policy_src = os.path.join(resource_dir, "pympacds.conf.in")
    policy_dst = "/etc/dbus-1/system.d/pympacds.conf"

    if os.path.exists(policy_src):
        _install_template(policy_src, policy_dst, bus_prefix, args.dry_run)
    elif os.path.exists(policy_dst):
        # update mode: replace prefix in existing file
        _update_file_prefix(policy_dst, bus_prefix, args.dry_run)
    else:
        print(f"Warning: no policy template found at {policy_src}", file=sys.stderr)

    # 4. reload D-Bus
    if not args.dry_run:
        _run(["pkill", "-HUP", "dbus-daemon"], check=False)

    if not args.quiet:
        print("pympacds setup complete.")


def _install_template(src: str, dst: str, prefix: str, dry_run: bool) -> None:
    """Read template, replace @BUS_PREFIX@, write to destination."""
    with open(src) as f:
        content = f.read()
    content = content.replace("@BUS_PREFIX@", prefix)
    if dry_run:
        print(f"Would install: {dst}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        f.write(content)
    print(f"Installed: {dst}")


def _update_file_prefix(path: str, new_prefix: str, dry_run: bool) -> None:
    """Replace all occurrences of the old bus prefix in an existing file."""
    with open(path) as f:
        content = f.read()

    # detect old prefix heuristically
    old_prefix = None
    for line in content.splitlines():
        if 'own_prefix="' in line:
            old_prefix = line.split('"')[1]
            break
    if old_prefix and old_prefix != new_prefix:
        content = content.replace(old_prefix, new_prefix)
        if not dry_run:
            with open(path, "w") as f:
                f.write(content)
            print(f"Updated prefix in {path}: {old_prefix} -> {new_prefix}")
        else:
            print(f"Would update prefix in {path}: {old_prefix} -> {new_prefix}")
    else:
        print(f"No prefix change needed in {path}")


# ------------------------------------------------------------------
# install (service)
# ------------------------------------------------------------------


def _cmd_install(args) -> None:
    if not args.dry_run:
        _ensure_root()

    service_name = args.service_name

    # locate files — service packages ship .service and .conf in their
    # installed package data
    unit_src = _find_resource(f"pympacds-{service_name}.service")
    conf_src = _find_resource(f"{service_name}.conf")

    if args.dry_run:
        print(f"Service unit: {unit_src}")
        print(f"DBus policy:  {conf_src}")
        return

    unit_dst = f"/etc/systemd/system/pympacds-{service_name}.service"
    conf_dst = f"/etc/dbus-1/system.d/{service_name}.conf"

    shutil.copy(unit_src, unit_dst)
    print(f"Installed: {unit_dst}")

    if os.path.exists(conf_src):
        shutil.copy(conf_src, conf_dst)
        print(f"Installed: {conf_dst}")
        _run(["pkill", "-HUP", "dbus-daemon"], check=False)

    _run(["systemctl", "daemon-reload"])
    if not args.no_enable:
        _run(["systemctl", "enable", f"pympacds-{service_name}"])
    if args.start:
        _run(["systemctl", "start", f"pympacds-{service_name}"])


def _find_resource(filename: str) -> str:
    """Search for a resource file in standard locations."""
    paths = [
        os.path.join("/usr/share/pympacds", filename),
        os.path.join("/usr/local/share/pympacds", filename),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[0]


# ------------------------------------------------------------------
# scaffolding
# ------------------------------------------------------------------


_SCAFFOLD_SERVICE_TEMPLATE = '''"""
{service_name} — pympacds service.
"""

import asyncio
import time
from pympacds.process import ProcessBase
from pympacds.dbus import DBusManager


class {class_name}(ProcessBase):
    def __init__(self):
        super().__init__(
            name="{service_name}",
            version="0.1.0",
            description="{service_name} service",
        )
        self._counter = 0
        self._start_time = int(time.time())

    async def start_dbus(self) -> bool:
        self.bus = DBusManager(
            logger=self.logger,
            busname="{service_name}",
        )
        # Register your D-Bus interfaces here:
        # self.bus.add_interface("main", MyContract("...", self))
        await self.bus.start()
        return True

    def update_tasks(self) -> None:
        if "periodic" not in self.tasklist:
            self.tasklist["periodic"] = asyncio.create_task(
                self._periodic(), name="periodic"
            )

    async def _periodic(self) -> None:
        try:
            while not self.exitevent.is_set():
                await self.do_waitexit(timeout=5)
                self._counter += 1
                self.logger.debug("Tick %d", self._counter)
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    {class_name}().start()
'''


def _cmd_new(args) -> None:
    name = args.service_name
    class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_"))

    # read bus prefix from installed D-Bus policy
    bus_prefix = "org.pympacds"
    policy_file = "/etc/dbus-1/system.d/pympacds.conf"
    if os.path.exists(policy_file):
        try:
            with open(policy_file) as f:
                for line in f:
                    if 'own_prefix="' in line:
                        bus_prefix = line.split('"')[1]
                        break
        except OSError:
            pass

    os.makedirs(name, exist_ok=True)
    os.makedirs(f"{name}/src/{name}", exist_ok=True)
    os.makedirs(f"{name}/config", exist_ok=True)
    os.makedirs(f"{name}/tests", exist_ok=True)

    # pyproject.toml
    with open(f"{name}/pyproject.toml", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            [build-system]
            requires = ["setuptools>=64"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "pympacds-{name}"
            version = "0.1.0"
            requires-python = ">=3.10"
            dependencies = ["pympacds"]

            [project.scripts]
            pympacds-{name} = "{name}.main:main"

            [tool.setuptools.packages.find]
            where = ["src"]
            """
            )
        )

    # main.py
    content = _SCAFFOLD_SERVICE_TEMPLATE.format(
        service_name=name, class_name=class_name, bus_prefix=bus_prefix
    )
    with open(f"{name}/src/{name}/main.py", "w") as f:
        f.write(content)

    # __init__.py
    with open(f"{name}/src/{name}/__init__.py", "w") as f:
        f.write("")

    # config template
    with open(f"{name}/config/{name}.ini", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            [DEFAULT]
            loglevel = INFO
            logstdout = false

            [dbus]
            bus_prefix = {bus_prefix}
            contract_health = true
            """
            )
        )

    # test stub
    with open(f"{name}/tests/test_main.py", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            import pytest
            from {name}.main import {class_name}


            def test_instantiate():
                svc = {class_name}()
                assert svc.name == "{name}"
            """
            )
        )

    print(f"Service scaffolded at ./{name}/")
    print(f"  cd {name} && pip install -e .")


# ------------------------------------------------------------------
# new-middleware
# ------------------------------------------------------------------


def _cmd_new_middleware(args) -> None:
    name = args.name
    pkg = f"pympacds_middleware_{name}"
    class_name = "".join(part.capitalize() for part in name.split("_"))

    os.makedirs(name, exist_ok=True)
    os.makedirs(f"{name}/src/{pkg}", exist_ok=True)
    os.makedirs(f"{name}/config", exist_ok=True)
    os.makedirs(f"{name}/tests", exist_ok=True)

    with open(f"{name}/pyproject.toml", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            [build-system]
            requires = ["setuptools>=64"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "pympacds-middleware-{name}"
            version = "0.1.0"
            requires-python = ">=3.10"
            dependencies = ["pympacds"]

            [project.entry-points."pympacds.middleware"]
            {name} = "{pkg}.middleware:{class_name}Middleware"

            [tool.setuptools.packages.find]
            where = ["src"]
            """
            )
        )

    with open(f"{name}/src/{pkg}/__init__.py", "w") as f:
        f.write("")

    with open(f"{name}/src/{pkg}/middleware.py", "w") as f:
        f.write(
            textwrap.dedent(
                f'''\
            """Middleware: {name}"""

            import asyncio
            from pympacds.middleware import MiddlewareBase


            class {class_name}Middleware(MiddlewareBase):
                async def setup(self) -> None:
                    """Add background tasks here."""
                    pass

                async def teardown(self) -> None:
                    """Clean up resources here."""
                    pass
            '''
            )
        )

    with open(f"{name}/config/{name}.ini.example", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            # Example configuration for {name} middleware
            # [{name}]
            # param1 = value1
            """
            )
        )

    with open(f"{name}/tests/test_middleware.py", "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
            import pytest
            from {pkg}.middleware import {class_name}Middleware


            def test_instantiate():
                assert {class_name}Middleware is not None
            """
            )
        )

    print(f"Middleware scaffolded at ./{name}/")
    print(f"  cd {name} && pip install -e .")


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


def _cmd_list(args) -> None:
    """List running pympacds services via D-Bus."""
    try:
        from . import get_dbus_aio, get_dbus_lib
        from .introspect import BusIntrospect

        async def _read_tags(bus, lib, name) -> str:
            """Best-effort read of provides/requires tags from a service."""
            tags = ""
            try:
                node = await bus.introspect(name, "/org/pympacds/health")
                bi = BusIntrospect(node.tostring(), path="/org/pympacds/health")
                for propname in ("provides", "requires"):
                    found = bi.find_property(propname)
                    if not found:
                        continue
                    iface = found[0][0]
                    reply = await bus.call(
                        lib.Message(
                            destination=name,
                            path="/org/pympacds/health",
                            interface="org.freedesktop.DBus.Properties",
                            member="Get",
                            body=[iface, propname],
                        )
                    )
                    tags += f" {propname}={list(reply.body[0].value)}"
            except Exception:
                pass
            return tags.strip()

        async def _list():
            aio = get_dbus_aio()
            lib = get_dbus_lib()
            bus = aio.MessageBus(bus_type=lib.BusType.SYSTEM)
            await bus.connect()
            try:
                names = await bus.call(
                    lib.Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="ListNames",
                    )
                )
                prefix = "org.pympacds"
                for name in sorted(names.body[0]):
                    if name.startswith(prefix):
                        # try to get PID
                        try:
                            pid_reply = await bus.call(
                                lib.Message(
                                    destination="org.freedesktop.DBus",
                                    path="/org/freedesktop/DBus",
                                    interface="org.freedesktop.DBus",
                                    member="GetConnectionUnixProcessID",
                                    body=[name],
                                )
                            )
                            pid = pid_reply.body[0]
                        except Exception:
                            pid = "?"
                        line = f"{name:<50} PID={pid}"
                        tags = await _read_tags(bus, lib, name)
                        if tags:
                            line += f"  {tags}"
                        print(line)
            finally:
                bus.disconnect()
                await bus.wait_for_disconnect()

        import asyncio

        asyncio.run(_list())
    except ImportError:
        print(
            "No D-Bus library available. Install dbus-fast or dbus-next.",
            file=sys.stderr,
        )
        sys.exit(1)


# ------------------------------------------------------------------
# config
# ------------------------------------------------------------------


def _validate_builtin(cp: object) -> list[str]:
    """Validate built-in [DEFAULT] and [dbus] keys. Returns list of error strings."""
    return _validate_default(cp) + _validate_dbus(cp)


def _validate_default(cp: object) -> list[str]:
    errors = []
    d = cp["DEFAULT"]
    lvl = d.get("loglevel", "WARN").upper()
    if lvl not in {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}:
        errors.append(f"DEFAULT.loglevel: invalid level '{lvl}'")
    try:
        d.getboolean("logstdout", False)
    except ValueError:
        errors.append("DEFAULT.logstdout: must be true/false")
    try:
        ms = d.getint("logmaxsize", 2**22)
        if ms <= 0:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("DEFAULT.logmaxsize: must be a positive integer")
    try:
        mc = d.getint("logmaxcount", 10)
        if mc < 0:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("DEFAULT.logmaxcount: must be a non-negative integer")
    return errors


def _validate_dbus(cp: object) -> list[str]:
    errors = []
    if not cp.has_section("dbus"):
        return errors
    d = cp["dbus"]
    _check_dbus_bus_type(d, errors)
    _check_dbus_drain_timeout(d, errors)
    _check_dbus_contract_flags(d, errors)
    _check_dbus_heartbeat(d, errors)
    _check_dbus_discovery(d, errors)
    return errors


def _check_dbus_bus_type(d, errors: list[str]) -> None:
    bt = d.get("bus_type", "system")
    if bt not in {"system", "session"}:
        errors.append(f"dbus.bus_type: must be 'system' or 'session', got '{bt}'")


def _check_dbus_drain_timeout(d, errors: list[str]) -> None:
    try:
        dto = d.getint("drain_timeout_ms", 2000)
        if dto < 0:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("dbus.drain_timeout_ms: must be a non-negative integer")


def _check_dbus_contract_flags(d, errors: list[str]) -> None:
    for key in (
        "contract_health",
        "contract_metrics",
        "contract_lifecycle",
        "contract_config",
    ):
        if key in d:
            try:
                d.getboolean(key)
            except ValueError:
                errors.append(f"dbus.{key}: must be true/false")


def _check_dbus_heartbeat(d, errors: list[str]) -> None:
    if "heartbeat_interval_s" in d:
        try:
            hb = d.getint("heartbeat_interval_s")
            if hb < 5 or hb > 3600:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("dbus.heartbeat_interval_s: must be an integer between 5 and 3600")


def _check_dbus_discovery(d, errors: list[str]) -> None:
    if "discovery_enabled" in d:
        try:
            d.getboolean("discovery_enabled")
        except ValueError:
            errors.append("dbus.discovery_enabled: must be true/false")


def _add_config_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default="", help="Config file path")


def _cmd_config(args) -> None:
    import configparser

    cfg_cmd = args.cfg_command
    if cfg_cmd is None:
        print("Use: pympacds-admin config {show,get,set,remove,validate}")
        return

    cp = configparser.ConfigParser()
    config_file = getattr(args, "config", None) or ""
    if config_file:
        cp.read(config_file)

    if cfg_cmd == "show":
        _cmd_config_show(cp)
    elif cfg_cmd == "get":
        _cmd_config_get(cp, args)
    elif cfg_cmd == "set":
        _cmd_config_set(cp, args, config_file)
    elif cfg_cmd == "remove":
        _cmd_config_remove(cp, args, config_file)
    elif cfg_cmd == "validate":
        _cmd_config_validate(cp, args)


def _cmd_config_show(cp) -> None:
    for section in cp.sections():
        print(f"[{section}]")
        for k, v in cp[section].items():
            print(f"  {k} = {v}")


def _cmd_config_get(cp, args) -> None:
    try:
        print(cp[args.section][args.key])
    except KeyError:
        print(f"Key '{args.section}.{args.key}' not found.", file=sys.stderr)
        sys.exit(1)


def _cmd_config_set(cp, args, config_file: str) -> None:
    if not config_file:
        print("--config is required for 'set'", file=sys.stderr)
        sys.exit(1)
    if not cp.has_section(args.section):
        cp.add_section(args.section)
    cp[args.section][args.key] = args.value
    with open(config_file, "w") as f:
        cp.write(f)


def _cmd_config_remove(cp, args, config_file: str) -> None:
    if not config_file:
        print("--config is required for 'remove'", file=sys.stderr)
        sys.exit(1)
    cp.remove_option(args.section, args.key)
    with open(config_file, "w") as f:
        cp.write(f)


def _cmd_config_validate(cp, args) -> None:
    errors = _validate_builtin(cp)
    schema = getattr(args, "schema", None)
    if schema:
        from .config import ConfigManager

        cm = ConfigManager(schema_file=schema)
        errors.extend(cm.validate(cp))
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    else:
        print("Configuration is valid.")


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _ensure_root() -> None:
    if os.geteuid() != 0:
        print(
            "pympacds-admin: error: root privileges required. Run with sudo.",
            file=sys.stderr,
        )
        sys.exit(1)


def _run(cmd: list, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=quiet, check=check)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        if not quiet:
            print(f"Command failed: {' '.join(cmd)}: {exc}", file=sys.stderr)
        if check:
            sys.exit(1)
        return subprocess.CompletedProcess(cmd, 1)

"""pympacds-admin — CLI administration tool."""

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import textwrap
from typing import Any

from . import realtime, security


def _version() -> str:
    """Return the pympacds package version (best effort)."""
    try:
        from importlib.metadata import version

        return version("pympacds")
    except Exception:  # noqa: BLE001
        pass
    # Fallback for running from a source checkout (not installed).
    pyproject = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "pyproject.toml")
    )
    try:
        with open(pyproject, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version = "):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"


_EPILOG = """\
commands:
  setup            Install framework-wide files (system user, log dir, D-Bus policy)
  install          Install a service: systemd unit, D-Bus policy, and [rt] drop-in
  new              Scaffold a new service package
  new-middleware   Scaffold a new middleware package
  list             List running pympacds services on the D-Bus system bus
  config           Inspect and edit INI configuration files

examples:
  # One-time system setup (system user, /var/log/pympacds, D-Bus policy)
  sudo pympacds-admin setup --bus-prefix org.pympacds

  # Register a service shipped by a package (uses its own unit and D-Bus policy)
  sudo pympacds-admin install mqtt --start

  # Register a CUSTOM program with a CUSTOM config file
  sudo pympacds-admin install mysvc \\
      --program /usr/local/bin/myapp \\
      --config /etc/myapp/myapp.ini --start

  # Inspect and edit a service configuration
  pympacds-admin config show -c /etc/pympacds/mqtt.ini
  sudo pympacds-admin config set -c /etc/pympacds/mqtt.ini mqtt qos 1

Run 'pympacds-admin COMMAND --help' for command-specific options.
"""


# Default systemd unit used when generating a unit for a custom program
# (fallback if the shipped pympacds@.service.in template is not installed).
_DEFAULT_UNIT = """\
[Unit]
Description=pympacds service: {service}
After=dbus.service
Requires=dbus.service

[Service]
Type=simple
User={user}
ExecStart={program} -c {config}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def main() -> None:
    """Entry point for ``pympacds-admin``."""
    parser = argparse.ArgumentParser(
        prog="pympacds-admin",
        description="pympacds administration tool — set up the framework, "
        "install/register services, and manage their INI configuration.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # setup
    sp_setup = sub.add_parser(
        "setup",
        help="Install framework-wide files",
        description="Create the pympacds system user and log directory and install the "
        "D-Bus security policy (requires root).",
    )
    _add_setup_args(sp_setup)

    # install
    sp_install = sub.add_parser(
        "install",
        help="Install a service (systemd unit + D-Bus policy + [rt] drop-in)",
        description="Install a service. By default the systemd unit and D-Bus policy "
        "shipped by the service package are copied. With --program, a unit is "
        "generated from the framework template so a CUSTOM executable and config "
        "file can be registered (requires root).",
    )
    sp_install.add_argument("service_name", metavar="SERVICE", help="Service name")
    sp_install.add_argument(
        "-c",
        "--config",
        default="",
        metavar="FILE",
        help="Service INI file: read for the [rt] section and used as the config path "
        "in the generated unit (default /etc/pympacds/SERVICE.ini)",
    )
    sp_install.add_argument(
        "--program",
        default="",
        metavar="PATH",
        help="Executable to run; setting it generates the unit from the framework "
        "template (default /usr/bin/pympacds-SERVICE)",
    )
    sp_install.add_argument(
        "--user",
        default=None,
        metavar="USER",
        help="System user the service runs as (default pympacds-user)",
    )
    sp_install.add_argument(
        "--supp-groups",
        default=None,
        metavar="GROUPS",
        help="Supplementary groups for the service (SupplementaryGroups=); "
        "comma/space-separated existing group names",
    )
    sp_install.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply the default systemd hardening baseline (default: --strict; "
        "use --no-strict for initial testing only)",
    )
    sp_install.add_argument("--no-enable", action="store_true", help="Skip systemctl enable")
    sp_install.add_argument("--start", action="store_true", help="Start the service after install")
    sp_install.add_argument("--dry-run", action="store_true", help="Print actions; change nothing")

    # new
    sp_new = sub.add_parser(
        "new",
        help="Scaffold a new service",
        description="Create a new service package skeleton in ./NAME/.",
    )
    sp_new.add_argument("service_name", metavar="NAME", help="Name of the service to create")

    # new-middleware
    sp_nmw = sub.add_parser(
        "new-middleware",
        help="Scaffold a new middleware package",
        description="Create a new middleware package skeleton in ./NAME/.",
    )
    sp_nmw.add_argument("name", metavar="NAME", help="Name of the middleware to create")

    # list
    sub.add_parser(
        "list",
        help="List running pympacds services on the bus",
        description="List pympacds services currently registered on the D-Bus system bus.",
    )

    # config
    sp_cfg = sub.add_parser(
        "config",
        help="Configuration management",
        description="Inspect and edit an INI configuration file.",
    )
    cfg_sub = sp_cfg.add_subparsers(dest="cfg_command", metavar="SUBCOMMAND")

    cfg_show = cfg_sub.add_parser(
        "show",
        help="Display current configuration",
        description="Print all sections and keys of the config file.",
    )
    _add_config_file_arg(cfg_show)

    cfg_get = cfg_sub.add_parser(
        "get", help="Get a config value", description="Print a single key's value."
    )
    cfg_get.add_argument("section", metavar="SECTION", help="INI section")
    cfg_get.add_argument("key", metavar="KEY", help="Config key")
    _add_config_file_arg(cfg_get)

    cfg_set = cfg_sub.add_parser(
        "set",
        help="Set a config value",
        description="Set a key, creating the section if needed, and write the file.",
    )
    cfg_set.add_argument("section", metavar="SECTION", help="INI section")
    cfg_set.add_argument("key", metavar="KEY", help="Config key")
    cfg_set.add_argument("value", metavar="VALUE", help="Config value")
    _add_config_file_arg(cfg_set)

    cfg_rm = cfg_sub.add_parser(
        "remove",
        help="Remove a config key",
        description="Remove a key from the config file.",
    )
    cfg_rm.add_argument("section", metavar="SECTION", help="INI section")
    cfg_rm.add_argument("key", metavar="KEY", help="Config key")
    _add_config_file_arg(cfg_rm)

    cfg_val = cfg_sub.add_parser(
        "validate",
        help="Validate config against schema",
        description="Validate built-in [DEFAULT]/[dbus]/[rt] keys and, optionally, a JSON schema.",
    )
    _add_config_file_arg(cfg_val)
    cfg_val.add_argument("-s", "--schema", metavar="FILE", help="JSON schema file (optional)")
    cfg_val.add_argument(
        "--program",
        metavar="PATH",
        default="",
        help="Also validate a program path's ownership/permissions (REQ-SEC-002)",
    )
    cfg_val.add_argument(
        "--user",
        metavar="USER",
        default=None,
        help="Service user for the permission checks (default pympacds-user)",
    )

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
    parser.add_argument(
        "--bus-prefix",
        default="org.pympacds",
        metavar="PREFIX",
        help="D-Bus bus-name prefix (default org.pympacds)",
    )
    parser.add_argument(
        "--resource-dir",
        default="/usr/share/pympacds",
        metavar="DIR",
        help="Directory holding the templates (pympacds.conf.in, pympacds@.service.in)",
    )
    parser.add_argument(
        "--no-dirs",
        action="store_true",
        help="Do not create the system user or the log directory",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the completion message")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print actions without changing the system"
    )


def _ensure_system_user(dry_run: bool) -> None:
    """Create the pympacds system user if missing (no-op in dry-run)."""
    if dry_run:
        print("Would create system user: pympacds-user")
        return
    if _run(["id", "pympacds-user"], check=False, quiet=True).returncode != 0:
        _run(["adduser", "--system", "--no-create-home", "--group", "pympacds-user"])
        _run(["adduser", "pympacds-user", "dbus"], check=False)


def _ensure_log_dir(dry_run: bool) -> None:
    """Create the log directory if missing (no-op in dry-run)."""
    if dry_run:
        print("Would create directory: /var/log/pympacds")
        return
    os.makedirs("/var/log/pympacds", exist_ok=True)
    _run(["chown", "pympacds-user:pympacds-user", "/var/log/pympacds"])


def _cmd_setup(args) -> None:
    dry_run = getattr(args, "dry_run", False)
    if not dry_run:
        _ensure_root()

    resource_dir = args.resource_dir
    bus_prefix = args.bus_prefix

    # 1-2. system user and log directory
    if not args.no_dirs:
        _ensure_system_user(dry_run)
        _ensure_log_dir(dry_run)

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


def _render_unit(service_name: str, program: str, user: str, config_path: str) -> str:
    """Render a systemd unit for a service from the shipped template.

    Falls back to a built-in unit when the template is not installed.
    """
    tpl = _find_resource("pympacds@.service.in")
    if os.path.exists(tpl):
        with open(tpl) as f:
            content = f.read()
        content = content.replace("@EXEC_START@", program)
        content = content.replace("@USER@", user)
        content = content.replace("%i", service_name)
        content = content.replace(f"/etc/pympacds/{service_name}.ini", config_path)
        return content
    return _DEFAULT_UNIT.format(
        service=service_name, user=user, program=program, config=config_path
    )


def _copy_policy(conf_src: str, conf_dst: str, dry_run: bool) -> None:
    """Install the service D-Bus policy if the package ships one."""
    if not os.path.exists(conf_src):
        return
    if dry_run:
        print(f"DBus policy:   {conf_src} -> {conf_dst}")
        return
    shutil.copy(conf_src, conf_dst)
    print(f"Installed: {conf_dst}")
    _run(["pkill", "-HUP", "dbus-daemon"], check=False)


def _install_generated_unit(
    unit_dst: str, unit_body: str, conf_src: str, conf_dst: str, dry_run: bool
) -> None:
    """Write a unit generated from the template for a custom program."""
    if dry_run:
        print(f"Generate unit: {unit_dst}")
        print(unit_body)
        _copy_policy(conf_src, conf_dst, dry_run=True)
        return
    os.makedirs(os.path.dirname(unit_dst), exist_ok=True)
    with open(unit_dst, "w") as f:
        f.write(unit_body)
    print(f"Installed: {unit_dst}")
    _copy_policy(conf_src, conf_dst, dry_run=False)


def _install_packaged_unit(
    unit_src: str, unit_dst: str, conf_src: str, conf_dst: str, dry_run: bool
) -> None:
    """Copy the systemd unit shipped by a service package."""
    if dry_run:
        print(f"Service unit: {unit_src}")
        print(f"DBus policy:  {conf_src}")
        return
    shutil.copy(unit_src, unit_dst)
    print(f"Installed: {unit_dst}")
    _copy_policy(conf_src, conf_dst, dry_run=False)


def _write_dropin(path: str, body: str, dry_run: bool) -> None:
    """Write (or preview, under ``--dry-run``) a systemd drop-in file."""
    if dry_run:
        print(f"Would write: {path}")
        print(body)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    print(f"Installed: {path}")


def _rt_is_deadline(service_name: str, args: Any) -> bool:
    """True when the service config requests ``[rt] sched_policy = deadline``.

    Used to omit ``NoNewPrivileges=`` from the hardening baseline for RT units
    (REQ-SEC-009 §C / REQ-RT-004).
    """
    config_path = getattr(args, "config", "") or f"/etc/pympacds/{service_name}.ini"
    cp = configparser.ConfigParser()
    if config_path and os.path.exists(config_path):
        cp.read(config_path)
    return cp.has_option("rt", "sched_policy") and (
        cp.get("rt", "sched_policy").strip().lower() == "deadline"
    )


def _apply_systemd(service_name: str, no_enable: bool, start: bool) -> None:
    """Reload systemd and optionally enable/start the service unit."""
    _run(["systemctl", "daemon-reload"])
    if not no_enable:
        _run(["systemctl", "enable", f"pympacds-{service_name}"])
    if start:
        _run(["systemctl", "start", f"pympacds-{service_name}"])


def _refuse_unsafe_targets(service_name: str, program: str, config_path: str, user: str) -> None:
    """Fail closed when a privileged program/config path is unsafe (REQ-SEC-002..004)."""
    problems: list[str] = []
    if program:
        problems += security.check_exec_target(program, user)
    if config_path and os.path.exists(config_path):
        problems += security.check_config_target(config_path, user)
    if not problems:
        return
    print(f"error: unsafe path for a service running as '{user}':", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        f"pympacds-admin: refusing to install pympacds-{service_name}.service",
        file=sys.stderr,
    )
    sys.exit(1)


def _install_unit_files(
    service_name: str,
    program: str,
    user: str,
    config_path: str,
    user_arg: str | None,
    supp_groups: str | None,
    dropin_dir: str,
    conf_src: str,
    conf_dst: str,
    dry_run: bool,
) -> None:
    """Write the unit (generated or packaged) and an identity drop-in."""
    unit_dst = f"/etc/systemd/system/pympacds-{service_name}.service"
    if program:
        unit_body = _render_unit(service_name, program, user, config_path)
        _install_generated_unit(unit_dst, unit_body, conf_src, conf_dst, dry_run)
    else:
        unit_src = _find_resource(f"pympacds-{service_name}.service")
        _install_packaged_unit(unit_src, unit_dst, conf_src, conf_dst, dry_run)
    if user_arg is not None or supp_groups:
        _write_dropin(
            os.path.join(dropin_dir, "user.conf"),
            security.render_user_dropin(user, supplementary_groups=supp_groups),
            dry_run,
        )


def _install_hardening(
    service_name: str, args: Any, program: str, strict: bool, dropin_dir: str, dry_run: bool
) -> None:
    """Apply/remove the REQ-SEC-009 baseline drop-in for a generated unit."""
    if not program:
        if not strict:
            print(
                f"note: pympacds-{service_name}.service is pre-hardened; "
                "--no-strict does not relax the packaged unit.",
                file=sys.stderr,
            )
        return
    hardening = os.path.join(dropin_dir, "hardening.conf")
    if strict:
        _write_dropin(
            hardening,
            security.render_hardening_dropin(rt_deadline=_rt_is_deadline(service_name, args)),
            dry_run,
        )
        return
    _remove_dropin(hardening, dry_run)
    print(
        f"warning: pympacds-{service_name}.service installed WITHOUT the hardening "
        "baseline (--no-strict); do not use in production.",
        file=sys.stderr,
    )


def _validated_identity(
    service_name: str, user: str, user_arg: str | None, supp_groups: str | None
) -> str | None:
    """Validate identity/groups (REQ-SEC-014); return the normalized groups."""
    problems = security.validate_service_user(user, require_exists=user_arg is not None)
    normalized, group_problems = security.parse_supplementary_groups(supp_groups or "")
    problems += group_problems
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        sys.exit(1)
    risky = security.sensitive_supplementary_groups(normalized) if normalized else []
    if risky:
        print(
            "warning: granting high-risk supplementary group(s) to "
            f"pympacds-{service_name}.service: " + ", ".join(risky),
            file=sys.stderr,
        )
    return normalized or None


def _confirm_root_or_exit(service_name: str, user: str, dry_run: bool) -> None:
    """Require an interactive confirmation before installing a root unit."""
    if security.is_root_user(user) and not security.confirm_root(service_name, dry_run=dry_run):
        print(
            f"pympacds-admin: refusing to install pympacds-{service_name}.service "
            "as root without confirmation",
            file=sys.stderr,
        )
        sys.exit(1)


def _apply_systemd_if_needed(service_name: str, args: Any, dry_run: bool) -> None:
    if not dry_run:
        _apply_systemd(
            service_name,
            getattr(args, "no_enable", False),
            getattr(args, "start", False),
        )


def _cmd_install(args) -> None:
    service_name = args.service_name
    dry_run = args.dry_run
    program = getattr(args, "program", "") or ""
    config_path = getattr(args, "config", "") or f"/etc/pympacds/{service_name}.ini"
    user_arg = getattr(args, "user", None)
    user = user_arg or security.DEFAULT_SERVICE_USER
    strict = getattr(args, "strict", True)

    if not dry_run:
        _ensure_root()

    supp_groups = _validated_identity(
        service_name, user, user_arg, getattr(args, "supp_groups", None)
    )
    _confirm_root_or_exit(service_name, user, dry_run)
    _refuse_unsafe_targets(service_name, program, config_path, user)

    dropin_dir = f"/etc/systemd/system/pympacds-{service_name}.service.d"
    conf_src = _find_resource(f"{service_name}.conf")
    conf_dst = f"/etc/dbus-1/system.d/{service_name}.conf"
    _install_unit_files(
        service_name,
        program,
        user,
        config_path,
        user_arg,
        supp_groups,
        dropin_dir,
        conf_src,
        conf_dst,
        dry_run,
    )
    _install_hardening(service_name, args, program, strict, dropin_dir, dry_run)

    rt_failed = _install_realtime(service_name, args, dry_run=dry_run)
    _apply_systemd_if_needed(service_name, args, dry_run)

    if rt_failed:
        sys.exit(1)


def _install_realtime(service_name: str, args: Any, dry_run: bool) -> bool:
    """Generate/remove the RT drop-in from the service's ``[rt]`` section.

    Returns True when a requested RT configuration was disabled because of
    validation errors (the caller then exits non-zero).  The service itself is
    always installed (REQ-RT-004/006).
    """
    unit = f"pympacds-{service_name}.service"
    dropin_dir = f"/etc/systemd/system/{unit}.d"
    dropin = os.path.join(dropin_dir, "rt.conf")

    cfg, findings = _read_service_rt(service_name, args)
    for finding in findings:
        if not finding.is_error:
            print(f"warning: [rt] {finding.format()}", file=sys.stderr)

    errors = realtime.errors_of(findings)
    if cfg is None:
        # no [rt] section: disabling RT is symmetric — drop any stale file
        _remove_dropin(dropin, dry_run)
        return False

    if errors:
        _report_rt_disabled(unit, errors)
        _remove_dropin(dropin, dry_run, announce=False)
        return True

    return _write_rt_dropin(unit, dropin, dropin_dir, cfg, dry_run)


def _read_service_rt(service_name: str, args: Any) -> tuple[Any, list]:
    """Read and parse the service's ``[rt]`` section (INI only; no env)."""
    config_path = getattr(args, "config", "") or f"/etc/pympacds/{service_name}.ini"
    cp = configparser.ConfigParser()
    if config_path and os.path.exists(config_path):
        cp.read(config_path)
    return realtime.load_rt(cp, environ={})


def _write_rt_dropin(
    unit: str,
    dropin: str,
    dropin_dir: str,
    cfg: Any,
    dry_run: bool,
) -> bool:
    """Write the RT drop-in and verify the unit; return True if RT was disabled."""
    body = realtime.render_dropin(cfg)
    if dry_run:
        print(f"Would write: {dropin}")
        print(body)
        return False
    os.makedirs(dropin_dir, exist_ok=True)
    with open(dropin, "w") as f:
        f.write(body)
    print(f"Installed: {dropin}")
    if _systemd_analyze_verify(unit) is False:
        print(
            f"error: systemd-analyze verify failed for {unit}; removing RT drop-in",
            file=sys.stderr,
        )
        _remove_dropin(dropin, dry_run=False, announce=False)
        return True
    return False


def _report_rt_disabled(unit: str, errors: list[realtime.Finding]) -> None:
    """Print the explicit 'RT requested but failed' interface response."""
    print(
        f"[rt] found but invalid — RT is DISABLED for {unit}\n"
        f"(no rt.conf written; the service will run with normal scheduling):",
        file=sys.stderr,
    )
    for err in errors:
        print(f"  {err.format()}", file=sys.stderr)


def _remove_dropin(dropin: str, dry_run: bool, announce: bool = True) -> None:
    if not os.path.exists(dropin):
        return
    if dry_run:
        if announce:
            print(f"Would remove: {dropin}")
        return
    os.remove(dropin)
    if announce:
        print(f"Removed: {dropin}")


def _systemd_analyze_verify(unit: str) -> bool | None:
    """Run ``systemd-analyze verify`` on the installed unit.

    Returns True (ok), False (failed), or None when systemd-analyze is
    unavailable (e.g. a container without systemd).
    """
    try:
        result = subprocess.run(
            ["systemd-analyze", "verify", unit],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            "warning: systemd-analyze not available; skipping RT unit verification",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        return False
    return True


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
    except Exception as exc:  # noqa: BLE001
        # No system bus reachable (e.g. in a container): not fatal for a listing.
        print(f"Unable to list services: {exc}", file=sys.stderr)


# ------------------------------------------------------------------
# config
# ------------------------------------------------------------------


def _validate_builtin(cp: Any) -> list[str]:
    """Validate built-in [DEFAULT], [dbus] and [rt] keys. Returns error strings."""
    return _validate_default(cp) + _validate_dbus(cp) + _validate_rt(cp)


def _validate_rt(cp: Any) -> list[str]:
    """Validate the [rt] section (strict: errors disable RT, REQ-RT-002/004)."""
    _cfg, findings = realtime.load_rt(cp, environ={})
    return [f"[rt] {finding.format()}" for finding in findings if finding.is_error]


def _validate_default(cp: Any) -> list[str]:
    errors: list[str] = []
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


def _validate_dbus(cp: Any) -> list[str]:
    errors: list[str] = []
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

    # REQ-SEC-007: when a --program is given (an install pre-flight), also check
    # the ownership/permissions of the program and its config. Plain validation of
    # an arbitrary INI (no --program) is a content check, not a privileged-path one.
    user = getattr(args, "user", None) or security.DEFAULT_SERVICE_USER
    program = getattr(args, "program", "") or ""
    config_file = getattr(args, "config", "") or ""
    if program:
        errors.extend(security.check_exec_target(program, user))
        if config_file and os.path.exists(config_file):
            errors.extend(security.check_config_target(config_file, user))

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


if __name__ == "__main__":
    main()

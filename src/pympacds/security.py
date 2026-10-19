"""Install-time security checks for ``pympacds-admin``.

Pure, testable helpers implementing REQ-SEC-002..009:

- the program/config permission predicate (:func:`check_exec_target`,
  :func:`check_config_target`) — fail-closed validation that a privileged service
  does not execute a program (or read a config) that a less-privileged user can
  modify;
- the ``systemd`` directive categories — ``ALWAYS_DIRECTIVES``,
  ``STRICT_DIRECTIVES``, ``INCOMPATIBLE_DIRECTIVES`` — and the ``--strict``
  drop-in rendering (REQ-SEC-009/013);
- the root confirmation gate (REQ-SEC-005).

The predicate is parameterised (``resolve``/``stat_of``) so tests can exercise it
with real metadata *and* with fabricated ``os.stat_result`` objects, without root
or ``chown`` (REQ-SEC-011).
"""

from __future__ import annotations

import os
import re
import stat
import sys

#: The system account created by the core (REQ-PACK-005); every service runs as it.
DEFAULT_SERVICE_USER = "pympacds-user"

#: Supplementary groups that are high-risk on typical systems when granted to a
#: service (a warning is emitted; the grant itself is intentional and not blocked).
SENSITIVE_GROUPS = frozenset(
    {
        "root",
        "wheel",
        "sudo",
        "admin",
        "disk",
        "docker",
        "libvirt",
        "kvm",
        "lxd",
        "incus",
        "shadow",
        "adm",
        "systemd-journal",
    }
)

#: Safe service-user / group names: no whitespace, ``=``, ``/`` or newlines, so a
#: value can never inject an extra systemd directive into a unit/drop-in.
_USER_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_GROUP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

#: ``always`` — emitted in both ``--strict`` and ``--no-strict``; these set
#: *privilege*, not *confinement*, and are never relaxed (REQ-SEC-008/013).
ALWAYS_DIRECTIVES = (
    "User=pympacds-user",
    "Group=pympacds-user",
)

#: ``strict`` — the default ``--strict`` baseline (REQ-SEC-009 §A). Only
#: directives that are safe for every service (including future/user-authored
#: ones) are emitted by the framework; removed by ``--no-strict``.
STRICT_DIRECTIVES = (
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "LogsDirectory=pympacds",
    "ProtectHome=yes",
    "PrivateTmp=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "ProtectProc=invisible",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "SystemCallArchitectures=native",
    "UMask=0027",
    "RemoveIPC=yes",
)

#: ``incompatible`` / not emitted — the framework never emits these; a service
#: declares them in its own unit only when its needs require them
#: (REQ-SEC-009 §B/§C). ``NoNewPrivileges=yes`` is ``strict`` but is omitted for
#: ``[rt] sched_policy = deadline`` (see ``_RT_INCOMPATIBLE``).
INCOMPATIBLE_DIRECTIVES = (
    "RestrictRealtime=yes",
    "PrivateDevices=",
    "DevicePolicy=",
    "DeviceAllow=",
    "ProcSubset=pid",
    "MemoryDenyWriteExecute=yes",
    "RestrictAddressFamilies=",
    "RestrictNamespaces=yes",
    "SystemCallFilter=",
    "CapabilityBoundingSet=",
)

#: Omitted for ``[rt] sched_policy = deadline`` units, where it conflicts with the
#: ambient ``CAP_SYS_NICE`` grant (REQ-RT-004).
_RT_INCOMPATIBLE = "NoNewPrivileges=yes"


def uid_for_user(user: str | None) -> int | None:
    """Return the uid for *user*, or ``None`` when it cannot be resolved."""
    if not user:
        return None
    try:
        import pwd

        return pwd.getpwnam(user).pw_uid
    except (KeyError, ImportError):
        return None


def is_root_user(user: str | None) -> bool:
    """True when *user* is (or resolves to) uid 0."""
    if user in ("root", "0"):
        return True
    return uid_for_user(user) == 0


def _user_exists(user: str) -> bool:
    try:
        import pwd
    except ImportError:  # pragma: no cover - non-POSIX
        return True
    try:
        if user.isdigit():
            pwd.getpwuid(int(user))
        else:
            pwd.getpwnam(user)
        return True
    except (KeyError, OverflowError):
        return False


def _group_exists(group: str) -> bool:
    try:
        import grp
    except ImportError:  # pragma: no cover - non-POSIX
        return True
    try:
        grp.getgrnam(group)
        return True
    except KeyError:
        return False


def validate_service_user(user: str, *, require_exists: bool = True) -> list[str]:
    """Reject an unsafe or unknown service user (REQ-SEC-014)."""
    if not user or not _USER_NAME_RE.match(user):
        return [f"invalid service user name '{user}'"]
    if require_exists and not _user_exists(user):
        return [f"unknown service user '{user}'"]
    return []


def parse_supplementary_groups(value: str) -> tuple[str, list[str]]:
    """Parse/validate ``--supp-groups`` into ``(normalized, problems)``.

    Comma- or whitespace-separated **group names** only (no numeric gids), each
    matching a safe name and resolving to an existing group. Unknown or malformed
    names are returned as problems so the caller can fail closed (REQ-SEC-014).
    """
    tokens = [t for t in value.replace(",", " ").split() if t]
    problems: list[str] = []
    normalized: list[str] = []
    for token in tokens:
        if not _GROUP_NAME_RE.match(token):
            problems.append(f"invalid supplementary group '{token}'")
        elif not _group_exists(token):
            problems.append(f"unknown supplementary group '{token}'")
        elif token not in normalized:
            normalized.append(token)
    return " ".join(normalized), problems


def sensitive_supplementary_groups(groups: str) -> list[str]:
    """Return the high-risk group names present in a normalized group string."""
    return [group for group in groups.split() if group in SENSITIVE_GROUPS]


def _mode(mode: int) -> str:
    return oct(stat.S_IMODE(mode))


def _ownership_problem(path: str, st, target_uid: int | None, user: str) -> str | None:
    if st.st_uid == 0 or st.st_uid == target_uid:
        return None
    return f"{path}: owned by uid {st.st_uid} (must be root or '{user}')"


def _writability_problem(path: str, st) -> str | None:
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"{path}: group/other-writable (mode {_mode(st.st_mode)})"
    return None


def _file_problems(
    path: str, st, target_uid: int | None, user: str, require_exec: bool
) -> list[str]:
    problems: list[str] = []
    if require_exec and not (st.st_mode & 0o111):
        problems.append(f"{path}: not executable")
    for problem in (
        _ownership_problem(path, st, target_uid, user),
        _writability_problem(path, st),
    ):
        if problem is not None:
            problems.append(problem)
    return problems


def _ancestor_problems(real: str, target_uid: int | None, user: str, stat_of) -> list[str]:
    problems: list[str] = []
    parent = os.path.dirname(real) or os.sep
    while True:
        try:
            pst = stat_of(parent)
        except OSError:
            problems.append(f"{parent}: not accessible")
            break
        for problem in (
            _ownership_problem(parent, pst, target_uid, user),
            _writability_problem(parent, pst),
        ):
            if problem is not None:
                problems.append(problem)
        if parent == os.sep:
            break
        parent = os.path.dirname(parent)
    return problems


def check_target(
    path: str,
    user: str,
    *,
    require_exec: bool,
    resolve=os.path.realpath,
    stat_of=os.lstat,
) -> list[str]:
    """Return the list of problems (empty == safe) for a privileged path.

    Implements REQ-SEC-002/003/004: the symlink-resolved regular file must be
    owned by ``root`` or the unit's *user* and must not be group/other-writable;
    **every** ancestor directory must likewise be owned by ``root`` or *user* and
    not group/other-writable. World-writable directories (e.g. ``/tmp``) are
    rejected outright — there is no sticky-bit exception.
    """
    target_uid = uid_for_user(user)
    real = resolve(path)
    try:
        st = stat_of(real)
    except OSError:
        return [f"{path}: not found"]

    if not stat.S_ISREG(st.st_mode):
        return [f"{real}: not a regular file"]
    problems = _file_problems(real, st, target_uid, user, require_exec)
    problems.extend(_ancestor_problems(real, target_uid, user, stat_of))
    return problems


def check_exec_target(path: str, user: str, **kwargs) -> list[str]:
    """Validate a service **program** (REQ-SEC-002); fail-closed."""
    kwargs.setdefault("require_exec", True)
    return check_target(path, user, **kwargs)


def check_config_target(path: str, user: str, **kwargs) -> list[str]:
    """Validate a service **config** file (REQ-SEC-003); fail-closed."""
    kwargs.setdefault("require_exec", False)
    return check_target(path, user, **kwargs)


def render_hardening_dropin(*, rt_deadline: bool = False) -> str:
    """Render the ``hardening.conf`` drop-in for the default (``--strict``) mode."""
    lines = [d for d in STRICT_DIRECTIVES if not (rt_deadline and d == _RT_INCOMPATIBLE)]
    return "[Service]\n" + "\n".join(lines) + "\n"


def render_user_dropin(
    user: str, group: str | None = None, supplementary_groups: str | None = None
) -> str:
    """Render the ``user.conf`` drop-in (identity and optional supplementary groups)."""
    group = group or user
    lines = [f"User={user}", f"Group={group}"]
    if supplementary_groups:
        lines.append(f"SupplementaryGroups={supplementary_groups}")
    return "[Service]\n" + "\n".join(lines) + "\n"


def root_alert(service_name: str) -> str:
    """The risk alert shown before an interactive root confirmation (REQ-SEC-005)."""
    return (
        f"WARNING: pympacds-{service_name}.service will run as ROOT.\n"
        "  - any bug or compromise in the service grants full root privileges;\n"
        "  - if the program or its config is writable by a non-root user, that user\n"
        "    can execute code as root (the file-permission checks still apply).\n"
        "  Proceed only if the service genuinely requires root."
    )


def confirm_root(
    service_name: str,
    *,
    dry_run: bool = False,
    isatty=None,
    input_fn=None,
    err=None,
) -> bool:
    """Print the root alert and require an **interactive** confirmation.

    There is deliberately **no** flag or environment variable that can pre-answer
    or automate this decision (REQ-SEC-005): without a TTY (or without an
    affirmative ``yes``) the caller must abort.
    """
    if err is None:
        err = sys.stderr
    print(root_alert(service_name), file=err)
    if dry_run:
        return True
    if isatty is None:
        isatty = sys.stdin.isatty
    if not isatty():
        print(
            "pympacds-admin: error: refusing to run as root without an interactive confirmation.",
            file=err,
        )
        return False
    if input_fn is None:
        input_fn = input
    try:
        answer = input_fn("Confirm running as root? [type 'yes']: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"

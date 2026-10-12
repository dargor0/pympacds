"""Real-time configuration support (REQ-RT-001 .. REQ-RT-012).

Owns everything about the framework-managed ``[rt]`` INI section:

* parsing and validation of the section (error/warning findings);
* ``PYMPACDS_RT_*`` environment overrides;
* translation of the section into ``systemd`` directives (used by
  ``pympacds-admin install`` to generate the RT drop-in);
* the two in-process settings ``systemd`` cannot express — ``mlockall`` and
  ``SCHED_DEADLINE`` — applied via ``ctypes``;
* the read-back verification of the effective settings.

The runtime is deliberately lenient: an invalid value is a finding, never a
startup error (REQ-RT-006).  The *admin* side is strict and disables RT when
any error is present (REQ-RT-004) — that policy lives in :mod:`pympacds.cli`.
"""

from __future__ import annotations

import configparser
import ctypes
import logging
import os
import platform
import re
import resource
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    """A single validation finding for the ``[rt]`` section."""

    key: str
    message: str
    severity: str = ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR

    def format(self) -> str:
        return f"{self.key}: {self.message}"


# ---------------------------------------------------------------------------
# scheduler / kernel constants
# ---------------------------------------------------------------------------

SCHED_OTHER = 0
SCHED_FIFO = 1
SCHED_RR = 2
SCHED_BATCH = 3
SCHED_IDLE = 5
SCHED_DEADLINE = 6

SCHED_FLAG_RESET_ON_FORK = 0x01

_POLICY_NAMES = {
    SCHED_OTHER: "other",
    SCHED_FIFO: "fifo",
    SCHED_RR: "rr",
    SCHED_BATCH: "batch",
    SCHED_IDLE: "idle",
    SCHED_DEADLINE: "deadline",
}
_POLICY_VALUES = {v: k for k, v in _POLICY_NAMES.items()}
_IO_CLASSES = {"realtime", "best-effort", "idle"}

_MCL_CURRENT = 0x01
_MCL_FUTURE = 0x02

_PR_GET_TIMERSLACK = 30

_IOPRIO_WHO_PROCESS = 1

# ---------------------------------------------------------------------------
# Raw syscall fallback
# ---------------------------------------------------------------------------
#
# These ``__NR_*`` numbers are a **fallback only**: modern C libraries export
# the ``sched_setattr()`` / ``sched_getattr()`` / ``ioprio_get()`` wrappers and
# are used first (see ``_sched_setattr`` / ``_sched_getattr`` / ``_ioprio_get``).
# The table is consulted only when the running libc does not export the wrapper
# (glibc added the ``sched_setattr``/``sched_getattr`` wrappers in 2.41, 2025).
#
# Linux syscall numbers are **ABI-frozen**: once a number is assigned for an
# architecture it is never changed or reused (doing so would break every
# existing binary), so these values do not drift.  To verify them, consult the
# kernel UAPI sources listed below (or ``ausyscall --dump`` / the assembled
# ``arch/<arch>/include/generated/uapi/asm/unistd_*.h``):
#
#   x86_64    arch/x86/entry/syscalls/syscall_64.tbl
#   i386      arch/x86/entry/syscalls/syscall_32.tbl
#   arm       arch/arm/tools/syscall.tbl           (32-bit EABI; armv6l/armv7l/armv8l)
#   aarch64   include/uapi/asm-generic/unistd.h    (64-bit generic)
#   riscv64   include/uapi/asm-generic/unistd.h    (64-bit generic)
#
# ``__NR_sched_setattr`` / ``__NR_sched_getattr`` (Linux >= 3.14) and
# ``__NR_ioprio_get``.  Architectures not listed here are treated as
# "unsupported" and real-time scheduling is skipped (non-fatal).
_SYSCALLS: dict[str, dict[str, int]] = {
    "x86_64": {"setattr": 314, "getattr": 315, "ioprio_get": 252},
    "i386": {"setattr": 351, "getattr": 352, "ioprio_get": 290},
    "arm": {"setattr": 380, "getattr": 381, "ioprio_get": 315},
    "aarch64": {"setattr": 274, "getattr": 275, "ioprio_get": 31},
    "riscv64": {"setattr": 274, "getattr": 275, "ioprio_get": 31},
}


def _normalize_machine(machine: str) -> str:
    """Map ``platform.machine()`` to a key in :data:`_SYSCALLS`."""
    m = machine.lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m == "riscv64":
        return "riscv64"
    if m in ("i386", "i486", "i586", "i686", "x86"):
        return "i386"
    if m.startswith("arm"):
        return "arm"
    return m


# ---------------------------------------------------------------------------
# configuration model
# ---------------------------------------------------------------------------


@dataclass
class RTConfig:
    """Parsed, sanitized ``[rt]`` configuration (all values at defaults when
    the corresponding key is absent)."""

    nice: int = 0
    sched_policy: str = "other"
    sched_priority: int = 0
    sched_runtime_ns: int = 0
    sched_deadline_ns: int = 0
    sched_period_ns: int = 0
    sched_reset_on_fork: bool = False
    cpu_affinity: str = ""
    io_sched_class: str = ""
    io_sched_priority: int = -1
    memlock: str = ""
    mlockall: bool = False
    oom_score_adjust: int = 0
    timer_slack_ns: int = -1


_KEY_TYPES: dict[str, type] = {
    "nice": int,
    "sched_policy": str,
    "sched_priority": int,
    "sched_runtime_ns": int,
    "sched_deadline_ns": int,
    "sched_period_ns": int,
    "sched_reset_on_fork": bool,
    "cpu_affinity": str,
    "io_sched_class": str,
    "io_sched_priority": int,
    "memlock": str,
    "mlockall": bool,
    "oom_score_adjust": int,
    "timer_slack_ns": int,
}

_DEFAULTS = RTConfig()

_CPU_LIST_RE = re.compile(r"^\d+(?:[-,]\d+)*$")
_MEMLOCK_RE = re.compile(r"^(?:infinity|\d+)$")


# ---------------------------------------------------------------------------
# parsing / validation
# ---------------------------------------------------------------------------


def _coerce(key: str, raw: str) -> tuple[Any, str | None]:
    """Coerce a raw INI/env string to the key's declared type."""
    typ = _KEY_TYPES[key]
    if typ is bool:
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True, None
        if low in ("0", "false", "no", "off"):
            return False, None
        return None, f"expected bool, got {raw!r}"
    if typ is int:
        try:
            return int(raw.strip()), None
        except ValueError:
            return None, f"expected int, got {raw!r}"
    return raw, None


def load_rt(
    cp: configparser.ConfigParser,
    environ: Mapping[str, str] | None = None,
) -> tuple[RTConfig | None, list[Finding]]:
    """Parse the ``[rt]`` section.

    Returns ``(None, [])`` when there is no ``[rt]`` section (RT disabled).
    Otherwise returns a *sanitized* config (invalid keys reset to their
    defaults) plus the list of findings.  ``PYMPACDS_RT_*`` overrides apply
    only within a present section.
    """
    if not cp.has_section("rt"):
        return None, []

    env = os.environ if environ is None else environ
    cfg = RTConfig()
    findings: list[Finding] = []

    for key in _KEY_TYPES:
        raw: str | None = None
        src = ""
        env_name = "PYMPACDS_RT_" + key.upper()
        if env_name in env:
            raw, src = env[env_name], "env"
        elif cp.has_option("rt", key):
            raw, src = cp.get("rt", key), "ini"
        if raw is None:
            continue
        value, err = _coerce(key, raw)
        if err is not None:
            findings.append(Finding(f"rt.{key}", f"{err} (from {src})", ERROR))
            continue
        setattr(cfg, key, value)

    findings.extend(_validate(cfg))
    return cfg, findings


_NS_KEYS = ("sched_runtime_ns", "sched_deadline_ns", "sched_period_ns")


def _err(findings: list[Finding], key: str, message: str) -> None:
    findings.append(Finding(f"rt.{key}", message, ERROR))


def _warn(findings: list[Finding], key: str, message: str) -> None:
    findings.append(Finding(f"rt.{key}", message, WARNING))


def _validate(cfg: RTConfig) -> list[Finding]:
    """Validate and sanitize ``cfg`` in place; return the findings."""
    findings: list[Finding] = []
    _validate_sched(cfg, findings)
    _validate_io(cfg, findings)
    _validate_memlock(cfg, findings)
    _validate_affinity(cfg, findings)
    _validate_oom_timer(cfg, findings)
    _warn_ineffective(cfg, findings)
    return findings


def _validate_sched(cfg: RTConfig, findings: list[Finding]) -> None:
    if not (-20 <= cfg.nice <= 19):
        _err(findings, "nice", f"out of range -20..19, got {cfg.nice}")
        cfg.nice = _DEFAULTS.nice

    if cfg.sched_policy not in _POLICY_VALUES:
        _err(findings, "sched_policy", f"unknown policy {cfg.sched_policy!r}")
        cfg.sched_policy = _DEFAULTS.sched_policy

    if cfg.sched_policy in ("fifo", "rr"):
        _validate_rt_policy(cfg, findings)
    elif cfg.sched_policy == "deadline":
        _validate_deadline(cfg, findings)
    else:
        _validate_other_policy(cfg, findings)


def _validate_rt_policy(cfg: RTConfig, findings: list[Finding]) -> None:
    if not (1 <= cfg.sched_priority <= 99):
        _err(
            findings,
            "sched_priority",
            f"required 1..99 for policy '{cfg.sched_policy}', got {cfg.sched_priority}",
        )
        cfg.sched_policy = _DEFAULTS.sched_policy
    _zero_ns_keys(cfg, findings)


def _validate_deadline(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.sched_priority != 0:
        _err(findings, "sched_priority", "must be 0 for policy 'deadline'")
        cfg.sched_priority = 0
    r, d, p = cfg.sched_runtime_ns, cfg.sched_deadline_ns, cfg.sched_period_ns
    if not (0 < r <= d <= p):
        _err(
            findings,
            "sched_runtime_ns",
            "deadline triple must satisfy 0 < runtime_ns <= deadline_ns <= period_ns "
            f"(got {r}, {d}, {p})",
        )
        cfg.sched_runtime_ns = cfg.sched_deadline_ns = cfg.sched_period_ns = 0
        cfg.sched_policy = _DEFAULTS.sched_policy


def _validate_other_policy(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.sched_priority != 0:
        _err(findings, "sched_priority", f"must be 0 for policy '{cfg.sched_policy}'")
        cfg.sched_priority = 0
    _zero_ns_keys(cfg, findings)


def _zero_ns_keys(cfg: RTConfig, findings: list[Finding]) -> None:
    for key in _NS_KEYS:
        if getattr(cfg, key) != 0:
            _err(findings, key, f"must be 0 for policy '{cfg.sched_policy}'")
            setattr(cfg, key, 0)


def _validate_io(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.io_sched_class and cfg.io_sched_class not in _IO_CLASSES:
        _err(findings, "io_sched_class", f"unknown class {cfg.io_sched_class!r}")
        cfg.io_sched_class = ""
    if cfg.io_sched_priority == -1:
        return
    if not (0 <= cfg.io_sched_priority <= 7):
        _err(findings, "io_sched_priority", f"out of range 0..7, got {cfg.io_sched_priority}")
        cfg.io_sched_priority = -1
    elif not cfg.io_sched_class:
        _err(findings, "io_sched_priority", "requires io_sched_class")
        cfg.io_sched_priority = -1


def _validate_memlock(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.memlock and not _MEMLOCK_RE.match(cfg.memlock):
        _err(findings, "memlock", f"expected 'infinity' or bytes, got {cfg.memlock!r}")
        cfg.memlock = ""
    if cfg.mlockall and not cfg.memlock:
        _err(findings, "mlockall", "memlock is required when mlockall = true")
        cfg.mlockall = False


def _validate_affinity(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.cpu_affinity and not _CPU_LIST_RE.match(cfg.cpu_affinity):
        _err(
            findings,
            "cpu_affinity",
            f"expected CPU list like '2,3' or '2-3', got {cfg.cpu_affinity!r}",
        )
        cfg.cpu_affinity = ""


def _validate_oom_timer(cfg: RTConfig, findings: list[Finding]) -> None:
    if not (-1000 <= cfg.oom_score_adjust <= 1000):
        _err(findings, "oom_score_adjust", f"out of range -1000..1000, got {cfg.oom_score_adjust}")
        cfg.oom_score_adjust = 0
    if cfg.timer_slack_ns != -1 and cfg.timer_slack_ns < 0:
        _err(findings, "timer_slack_ns", f"must be >= 0 or -1 (unset), got {cfg.timer_slack_ns}")
        cfg.timer_slack_ns = -1


def _warn_ineffective(cfg: RTConfig, findings: list[Finding]) -> None:
    if cfg.sched_policy in ("fifo", "rr", "idle") and cfg.nice != 0:
        _warn(findings, "nice", f"ignored by the kernel under policy '{cfg.sched_policy}'")


def errors_of(findings: list[Finding]) -> list[Finding]:
    """Return only the error-severity findings."""
    return [f for f in findings if f.is_error]


# ---------------------------------------------------------------------------
# effective levers
# ---------------------------------------------------------------------------


def _effective_levers(cfg: RTConfig) -> list[str]:
    """Keys that are applied/verified: the always-materialized baseline
    (``sched_policy``, ``nice``) plus every key set to a non-default value."""
    levers = {"sched_policy", "nice"}
    for f in fields(RTConfig):
        if getattr(cfg, f.name) != getattr(_DEFAULTS, f.name):
            levers.add(f.name)
    return sorted(levers)


# ---------------------------------------------------------------------------
# systemd directive mapping
# ---------------------------------------------------------------------------


def emit_systemd(cfg: RTConfig) -> list[str]:
    """Translate a (valid) ``[rt]`` config to systemd ``[Service]`` directives.

    Only non-default keys are emitted, except ``nice`` and ``sched_policy``
    which are always materialized (REQ-RT-002/004).
    """
    directives: list[str] = [f"Nice={cfg.nice}"]

    if cfg.sched_policy == "deadline":
        # systemd cannot express SCHED_DEADLINE; grant the capability and let
        # the framework set it in-process.
        directives.append("AmbientCapabilities=CAP_SYS_NICE")
        directives.append("RestrictRealtime=no")
    else:
        directives.append(f"CPUSchedulingPolicy={cfg.sched_policy}")
        if cfg.sched_policy in ("fifo", "rr"):
            directives.append(f"CPUSchedulingPriority={cfg.sched_priority}")
            directives.append("RestrictRealtime=no")
        if cfg.sched_reset_on_fork:
            directives.append("CPUSchedulingResetOnFork=yes")

    if cfg.cpu_affinity:
        directives.append(f"CPUAffinity={cfg.cpu_affinity}")
    if cfg.io_sched_class:
        directives.append(f"IOSchedulingClass={cfg.io_sched_class}")
        if cfg.io_sched_priority != -1:
            directives.append(f"IOSchedulingPriority={cfg.io_sched_priority}")
    if cfg.memlock:
        directives.append(f"LimitMEMLOCK={cfg.memlock}")
    if cfg.oom_score_adjust != 0:
        directives.append(f"OOMScoreAdjust={cfg.oom_score_adjust}")
    if cfg.timer_slack_ns != -1:
        directives.append(f"TimerSlackNSec={cfg.timer_slack_ns}")
    return directives


def render_dropin(cfg: RTConfig) -> str:
    """Render the ``rt.conf`` drop-in body for a config."""
    lines = ["[Service]"]
    lines.extend(emit_systemd(cfg))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ctypes plumbing
# ---------------------------------------------------------------------------


class _SchedAttr(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("sched_policy", ctypes.c_uint32),
        ("sched_flags", ctypes.c_uint64),
        ("sched_nice", ctypes.c_int32),
        ("sched_priority", ctypes.c_uint32),
        ("sched_runtime", ctypes.c_uint64),
        ("sched_deadline", ctypes.c_uint64),
        ("sched_period", ctypes.c_uint64),
    ]


def _libc() -> Any:
    try:
        lib = ctypes.CDLL(None, use_errno=True)
    except OSError:  # pragma: no cover - defensive
        return None
    for name in ("syscall", "prctl"):
        fn = getattr(lib, name, None)
        if fn is not None:
            try:
                fn.restype = ctypes.c_long  # type: ignore[attr-defined]
            except (AttributeError, TypeError):  # pragma: no cover - defensive
                pass
    return lib


def _syscall_number(name: str) -> int | None:
    """Look up ``__NR_<name>`` for the running architecture (fallback only)."""
    return _SYSCALLS.get(_normalize_machine(platform.machine()), {}).get(name)


def _sched_setattr(libc: Any, attr: _SchedAttr, flags: int) -> int | None:
    """Call ``sched_setattr(2)``; return the return code or None if unsupported.

    Prefers the libc wrapper (glibc >= 2.41); falls back to ``syscall(2)``.
    """
    wrapper = getattr(libc, "sched_setattr", None)
    if wrapper is not None:
        return int(wrapper(0, ctypes.byref(attr), flags))
    number = _syscall_number("setattr")
    if number is None:
        return None
    return int(libc.syscall(number, 0, ctypes.byref(attr), flags))


def _sched_getattr(libc: Any, attr: _SchedAttr, flags: int) -> int | None:
    """Call ``sched_getattr(2)``; return the return code or None if unsupported."""
    size = ctypes.sizeof(_SchedAttr)
    wrapper = getattr(libc, "sched_getattr", None)
    if wrapper is not None:
        return int(wrapper(0, ctypes.byref(attr), size, flags))
    number = _syscall_number("getattr")
    if number is None:
        return None
    return int(libc.syscall(number, 0, ctypes.byref(attr), size, flags))


def _ioprio_get(libc: Any, which: int, who: int) -> int | None:
    """Call ``ioprio_get(2)``; return the encoded value or None if unsupported."""
    wrapper = getattr(libc, "ioprio_get", None)
    if wrapper is not None:
        return int(wrapper(which, who))
    number = _syscall_number("ioprio_get")
    if number is None:
        return None
    return int(libc.syscall(number, which, who))


# ---------------------------------------------------------------------------
# in-process application
# ---------------------------------------------------------------------------


def apply_in_process(cfg: RTConfig, logger: logging.Logger | None = None) -> dict[str, bool]:
    """Apply the two settings systemd cannot express.

    Returns a mapping of ``{key: applied}`` (only for attempted settings).
    Every failure is logged at ``WARNING`` and is non-fatal (REQ-RT-006).
    """
    log = logger or logging.getLogger(__name__)
    applied: dict[str, bool] = {}
    if cfg.mlockall:
        applied["mlockall"] = _mlockall(log)
    if cfg.sched_policy == "deadline":
        applied["sched_policy"] = _set_deadline(cfg, log)
    return applied


def _mlockall(log: logging.Logger) -> bool:
    libc = _libc()
    if libc is None:  # pragma: no cover - defensive
        log.warning("rt.mlockall: libc unavailable; running unlocked")
        return False
    ctypes.set_errno(0)
    try:
        rc = libc.mlockall(_MCL_CURRENT | _MCL_FUTURE)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        log.warning("rt.mlockall: call failed; running unlocked")
        return False
    if rc != 0:
        log.warning(
            "rt.mlockall: mlockall() failed (%s); running unlocked",
            os.strerror(ctypes.get_errno()),
        )
        return False
    return True


def _set_deadline(cfg: RTConfig, log: logging.Logger) -> bool:
    libc = _libc()
    if libc is None:
        log.warning("rt.sched_policy: SCHED_DEADLINE unsupported on this platform; keeping default")
        return False

    attr = _SchedAttr()
    attr.size = ctypes.sizeof(_SchedAttr)
    attr.sched_policy = SCHED_DEADLINE
    attr.sched_flags = SCHED_FLAG_RESET_ON_FORK if cfg.sched_reset_on_fork else 0
    attr.sched_nice = 0
    attr.sched_priority = 0
    attr.sched_runtime = cfg.sched_runtime_ns
    attr.sched_deadline = cfg.sched_deadline_ns
    attr.sched_period = cfg.sched_period_ns

    ctypes.set_errno(0)
    try:
        rc = _sched_setattr(libc, attr, 0)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        log.warning("rt.sched_policy: sched_setattr() unavailable; keeping default")
        return False
    if rc is None:
        log.warning("rt.sched_policy: SCHED_DEADLINE unsupported on this arch; keeping default")
        return False
    if rc != 0:
        log.warning(
            "rt.sched_policy: SCHED_DEADLINE rejected (%s); reverting to SCHED_OTHER",
            os.strerror(ctypes.get_errno()),
        )
        _reset_other(log)
        return False
    return True


def _reset_other(log: logging.Logger) -> None:
    """Best-effort fall back to SCHED_OTHER (non-fatal)."""
    try:
        os.sched_setscheduler(0, SCHED_OTHER, os.sched_param(0))
    except (AttributeError, OSError):
        log.debug("rt.sched_policy: could not reset to SCHED_OTHER")


# ---------------------------------------------------------------------------
# read-back verification
# ---------------------------------------------------------------------------


def _read_nice() -> int | None:
    try:
        return os.getpriority(os.PRIO_PROCESS, 0)
    except (AttributeError, OSError):
        return None


def _read_policy() -> str | None:
    try:
        return _POLICY_NAMES.get(os.sched_getscheduler(0))
    except (AttributeError, OSError):
        return None


def _read_priority() -> int | None:
    try:
        return os.sched_getparam(0).sched_priority
    except (AttributeError, OSError):
        return None


def _read_affinity() -> list[int] | None:
    try:
        return sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None


def _read_deadline() -> dict[str, int] | None:
    libc = _libc()
    if libc is None:
        return None
    attr = _SchedAttr()
    attr.size = ctypes.sizeof(_SchedAttr)
    try:
        rc = _sched_getattr(libc, attr, 0)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        return None
    if rc is None or rc != 0:
        return None
    return {
        "runtime": int(attr.sched_runtime),
        "deadline": int(attr.sched_deadline),
        "period": int(attr.sched_period),
        "flags": int(attr.sched_flags),
        "policy": int(attr.sched_policy),
    }


def _read_memlock() -> int | None:
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    except (AttributeError, ValueError, OSError):
        return None
    return soft


def _read_vmlck() -> int | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmLck:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _read_oom_score_adj() -> int | None:
    try:
        with open("/proc/self/oom_score_adj") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_timer_slack() -> int | None:
    libc = _libc()
    if libc is None:
        return None
    try:
        rc = libc.prctl(_PR_GET_TIMERSLACK, 0, 0, 0, 0)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        return None
    if rc < 0:
        return None
    return int(rc)


def _read_ioprio() -> tuple[str, int] | None:
    libc = _libc()
    if libc is None:
        return None
    try:
        raw = _ioprio_get(libc, _IOPRIO_WHO_PROCESS, 0)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        return None
    if raw is None or raw < 0:
        return None
    value = int(raw)
    klass = (value >> 13) & 0x7
    data = value & 0x1FFF
    names = {0: "best-effort", 1: "realtime", 2: "best-effort", 3: "idle"}
    return names.get(klass, "best-effort"), data


def verify() -> dict[str, Any]:
    """Read back the effective settings for the levers (REQ-RT-009)."""
    return {
        "nice": _read_nice(),
        "sched_policy": _read_policy(),
        "sched_priority": _read_priority(),
        "sched_reset_on_fork": _read_deadline(),
        "cpu_affinity": _read_affinity(),
        "memlock": _read_memlock(),
        "mlockall": _read_vmlck(),
        "oom_score_adjust": _read_oom_score_adj(),
        "timer_slack_ns": _read_timer_slack(),
        "io_sched_class": _read_ioprio(),
    }


# ---------------------------------------------------------------------------
# status object
# ---------------------------------------------------------------------------


def _requested(key: str, cfg: RTConfig) -> Any:
    return getattr(cfg, key)


def _parse_cpu_list(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.update(range(int(lo), int(hi) + 1))
        elif part:
            cpus.add(int(part))
    return cpus


def _match_nice(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("nice")
    return eff, eff == cfg.nice


def _match_policy(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("sched_policy")
    return eff, eff == cfg.sched_policy


def _match_priority(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("sched_priority")
    return eff, eff == cfg.sched_priority


def _match_reset_on_fork(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    info = verified.get("sched_reset_on_fork") or {}
    eff = bool(info.get("flags", 0) & SCHED_FLAG_RESET_ON_FORK)
    return eff, eff == cfg.sched_reset_on_fork


def _match_affinity(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("cpu_affinity")
    if eff is None:
        return None, True
    return eff, set(eff) == _parse_cpu_list(cfg.cpu_affinity)


def _match_memlock(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("memlock")
    if eff is None:
        return None, True
    if cfg.memlock == "infinity":
        ok = eff == resource.RLIM_INFINITY
        return ("infinity" if ok else eff), ok
    return eff, eff >= int(cfg.memlock)


def _match_mlockall(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("mlockall")
    if eff is None:
        return None, True
    return eff, (eff > 0) == cfg.mlockall


def _match_oom_score(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("oom_score_adjust")
    return eff, eff == cfg.oom_score_adjust


def _match_timer_slack(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("timer_slack_ns")
    if eff is None:
        return None, True
    return eff, eff == cfg.timer_slack_ns


def _ns_matcher(field: str) -> _MatchFn:
    def matcher(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
        info = verified.get("sched_reset_on_fork") or {}
        eff = info.get(field)
        return eff, eff == getattr(cfg, "sched_" + field + "_ns")

    return matcher


def _match_io_class(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("io_sched_class")
    if eff is None:
        return None, True
    return eff[0], eff[0] == cfg.io_sched_class


def _match_io_priority(cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    eff = verified.get("io_sched_class")
    if eff is None:
        return None, True
    return eff[1], eff[1] == cfg.io_sched_priority


_MatchFn = Callable[[RTConfig, dict[str, Any]], tuple[Any, bool]]

_MATCHERS: dict[str, _MatchFn] = {
    "nice": _match_nice,
    "sched_policy": _match_policy,
    "sched_priority": _match_priority,
    "sched_reset_on_fork": _match_reset_on_fork,
    "cpu_affinity": _match_affinity,
    "memlock": _match_memlock,
    "mlockall": _match_mlockall,
    "oom_score_adjust": _match_oom_score,
    "timer_slack_ns": _match_timer_slack,
    "sched_runtime_ns": _ns_matcher("runtime"),
    "sched_deadline_ns": _ns_matcher("deadline"),
    "sched_period_ns": _ns_matcher("period"),
    "io_sched_class": _match_io_class,
    "io_sched_priority": _match_io_priority,
}


def _match(key: str, cfg: RTConfig, verified: dict[str, Any]) -> tuple[Any, bool]:
    """Return ``(effective_value, ok)`` for one lever."""
    matcher = _MATCHERS.get(key)
    if matcher is None:
        return None, True
    return matcher(cfg, verified)


def status_object(
    cfg: RTConfig,
    findings: list[Finding],
    applied: dict[str, bool] | None = None,
    verified: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``rt`` status object exposed via ``status()``/``ProcessBase.rt``."""
    applied = applied or {}
    verified = verified if verified is not None else verify()
    obj: dict[str, Any] = {"ok": True, "findings": [f.format() for f in findings]}
    for key in _effective_levers(cfg):
        eff, ok = _match(key, cfg, verified)
        obj[key] = {
            "requested": _jsonable(_requested(key, cfg)),
            "applied": applied.get(key),
            "verified": _jsonable(eff),
            "ok": ok,
        }
        if not ok:
            obj["ok"] = False
    return obj


def _jsonable(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    return value


def log_mismatches(
    cfg: RTConfig,
    status: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """Log a WARNING for every lever whose effective value does not match."""
    for key, entry in status.items():
        if isinstance(entry, dict) and not entry.get("ok", True):
            logger.warning(
                "rt.%s requested %r but effective is %r (install/restart via systemd, REQ-RT-004)",
                key,
                entry.get("requested"),
                entry.get("verified"),
            )

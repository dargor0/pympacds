"""Tests for the real-time ``[rt]`` support (REQ-RT-001 .. REQ-RT-012).

All tests run unprivileged: the privileged syscalls (``mlockall`` /
``sched_setattr``) are stubbed, and read-backs only query the caller's own
state.
"""

import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pympacds import realtime  # noqa: E402


def make_cp(**rt) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.add_section("rt")
    for k, v in rt.items():
        cp.set("rt", k, str(v))
    return cp


# ---------------------------------------------------------------------------
# parsing / validation
# ---------------------------------------------------------------------------


def test_no_section_disables_rt():
    cp = configparser.ConfigParser()
    cfg, findings = realtime.load_rt(cp)
    assert cfg is None
    assert findings == []


def test_empty_section_enables_defaults():
    cfg, findings = realtime.load_rt(configparser.ConfigParser())
    assert cfg is None

    cp = configparser.ConfigParser()
    cp.add_section("rt")
    cfg, findings = realtime.load_rt(cp)
    assert cfg is not None
    assert findings == []
    assert cfg.sched_policy == "other"
    assert cfg.nice == 0


def test_fifo_requires_priority():
    cfg, findings = realtime.load_rt(make_cp(sched_policy="fifo"))
    errors = realtime.errors_of(findings)
    assert any(f.key == "rt.sched_priority" for f in errors)
    # sanitized back to the default policy
    assert cfg.sched_policy == "other"


def test_fifo_valid():
    cfg, findings = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=20))
    assert realtime.errors_of(findings) == []
    assert cfg.sched_priority == 20


def test_deadline_triple_consistency():
    ok = make_cp(
        sched_policy="deadline",
        sched_runtime_ns=200_000,
        sched_deadline_ns=1_000_000,
        sched_period_ns=5_000_000,
    )
    cfg, findings = realtime.load_rt(ok)
    assert realtime.errors_of(findings) == []
    assert cfg.sched_policy == "deadline"

    bad = make_cp(
        sched_policy="deadline",
        sched_runtime_ns=2_000_000,
        sched_deadline_ns=1_000_000,
        sched_period_ns=5_000_000,
    )
    cfg, findings = realtime.load_rt(bad)
    assert any(f.key == "rt.sched_runtime_ns" for f in realtime.errors_of(findings))
    assert cfg.sched_policy == "other"


def test_ns_keys_forbidden_for_non_deadline():
    cfg, findings = realtime.load_rt(make_cp(sched_policy="other", sched_runtime_ns=5))
    assert any(f.key == "rt.sched_runtime_ns" for f in realtime.errors_of(findings))
    assert cfg.sched_runtime_ns == 0


def test_nice_range():
    cfg, findings = realtime.load_rt(make_cp(nice=99))
    assert any(f.key == "rt.nice" for f in realtime.errors_of(findings))
    assert cfg.nice == 0


def test_io_priority_requires_class():
    cfg, findings = realtime.load_rt(make_cp(io_sched_priority=4))
    assert any(f.key == "rt.io_sched_priority" for f in realtime.errors_of(findings))
    assert cfg.io_sched_priority == -1


def test_mlockall_requires_memlock():
    cfg, findings = realtime.load_rt(make_cp(mlockall="true"))
    assert any(f.key == "rt.mlockall" for f in realtime.errors_of(findings))
    assert cfg.mlockall is False

    cfg, findings = realtime.load_rt(make_cp(mlockall="true", memlock="infinity"))
    assert realtime.errors_of(findings) == []
    assert cfg.mlockall is True


def test_nice_warning_under_fifo():
    _cfg, findings = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=10, nice=-5))
    warnings = [f for f in findings if f.severity == realtime.WARNING]
    assert any(f.key == "rt.nice" for f in warnings)
    assert realtime.errors_of(findings) == []


def test_env_override_applies_within_present_section():
    cp = configparser.ConfigParser()
    cp.add_section("rt")
    cfg, findings = realtime.load_rt(cp, environ={"PYMPACDS_RT_NICE": "-7"})
    assert cfg is not None
    assert cfg.nice == -7


def test_env_does_not_enable_without_section():
    cp = configparser.ConfigParser()
    cfg, findings = realtime.load_rt(cp, environ={"PYMPACDS_RT_NICE": "-7"})
    assert cfg is None


def test_bad_env_value_is_error():
    cp = configparser.ConfigParser()
    cp.add_section("rt")
    cfg, findings = realtime.load_rt(cp, environ={"PYMPACDS_RT_NICE": "abc"})
    assert any(f.key == "rt.nice" for f in realtime.errors_of(findings))


# ---------------------------------------------------------------------------
# systemd mapping
# ---------------------------------------------------------------------------


def test_emit_empty_section_materializes_baseline():
    cfg, _ = realtime.load_rt(make_cp())
    directives = realtime.emit_systemd(cfg)
    assert "Nice=0" in directives
    assert "CPUSchedulingPolicy=other" in directives


def test_emit_fifo():
    cfg, _ = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=20))
    directives = realtime.emit_systemd(cfg)
    assert "CPUSchedulingPolicy=fifo" in directives
    assert "CPUSchedulingPriority=20" in directives
    assert "RestrictRealtime=no" in directives
    assert not any(d.startswith("LimitRTPRIO=") for d in directives)


def test_emit_deadline_grants_capability():
    cfg, _ = realtime.load_rt(
        make_cp(
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    directives = realtime.emit_systemd(cfg)
    assert "AmbientCapabilities=CAP_SYS_NICE" in directives
    assert "RestrictRealtime=no" in directives
    assert not any(d.startswith("CPUSchedulingPolicy=") for d in directives)


def test_emit_only_non_default_keys():
    cfg, _ = realtime.load_rt(make_cp(oom_score_adjust=-500))
    directives = realtime.emit_systemd(cfg)
    assert "OOMScoreAdjust=-500" in directives
    assert not any(d.startswith("CPUAffinity=") for d in directives)
    assert not any(d.startswith("LimitMEMLOCK=") for d in directives)


def test_render_dropin_has_service_header():
    cfg, _ = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=10))
    body = realtime.render_dropin(cfg)
    assert body.startswith("[Service]")
    assert "CPUSchedulingPolicy=fifo" in body


# ---------------------------------------------------------------------------
# apply (stubbed) and status
# ---------------------------------------------------------------------------


def test_apply_in_process_stubbed(monkeypatch):
    monkeypatch.setattr(realtime, "_mlockall", lambda log: True)
    monkeypatch.setattr(realtime, "_set_deadline", lambda cfg, log: True)
    cfg, _ = realtime.load_rt(
        make_cp(
            mlockall="true",
            memlock="infinity",
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    applied = realtime.apply_in_process(cfg)
    assert applied == {"mlockall": True, "sched_policy": True}


def test_status_object_reports_per_lever(monkeypatch):
    fake = {
        "nice": 0,
        "sched_policy": "fifo",
        "sched_priority": 20,
        "sched_reset_on_fork": {},
        "cpu_affinity": [0, 1],
        "memlock": None,
        "mlockall": None,
        "oom_score_adjust": 0,
        "timer_slack_ns": None,
        "io_sched_class": None,
    }
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=20))
    status = realtime.status_object(cfg, [])
    assert status["ok"] is True
    assert status["sched_policy"]["requested"] == "fifo"
    assert status["sched_policy"]["applied"] is None
    assert status["sched_policy"]["verified"] == "fifo"


def test_status_object_flags_mismatch(monkeypatch):
    fake = {"sched_policy": "other", "nice": 0, "sched_priority": 0}
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=20))
    status = realtime.status_object(cfg, [])
    assert status["ok"] is False
    assert status["sched_policy"]["ok"] is False


# ---------------------------------------------------------------------------
# read-backs (unprivileged)
# ---------------------------------------------------------------------------


def test_readbacks_do_not_raise():
    realtime._read_nice()
    realtime._read_policy()
    realtime._read_priority()
    realtime._read_affinity()
    realtime._read_memlock()
    realtime._read_oom_score_adj()
    realtime._read_timer_slack()
    realtime._read_ioprio()


def test_read_nice_matches_os():
    assert realtime._read_nice() == os.getpriority(os.PRIO_PROCESS, 0)


def test_read_policy_is_known():
    assert realtime._read_policy() in realtime._POLICY_NAMES.values()


def test_log_mismatches(caplog):
    import logging

    cfg, _ = realtime.load_rt(make_cp(sched_policy="fifo", sched_priority=20))
    status = {
        "sched_policy": {"requested": "fifo", "applied": None, "verified": "other", "ok": False}
    }
    with caplog.at_level(logging.WARNING):
        realtime.log_mismatches(cfg, status, logging.getLogger("test"))
    assert any("sched_policy" in r.message for r in caplog.records)


def test_finding_format():
    f = realtime.Finding("rt.nice", "bad")
    assert f.format() == "rt.nice: bad"
    assert f.is_error
    assert not realtime.Finding("rt.nice", "w", realtime.WARNING).is_error


def test_parse_cpu_list():
    assert realtime._parse_cpu_list("2,3") == {2, 3}
    assert realtime._parse_cpu_list("2-4") == {2, 3, 4}


@pytest.mark.parametrize(
    "text,valid",
    [("2,3", True), ("2-3", True), ("x", False), ("", True)],
)
def test_cpu_affinity_validation(text, valid):
    cfg, findings = realtime.load_rt(make_cp(cpu_affinity=text))
    has_error = any(f.key == "rt.cpu_affinity" for f in realtime.errors_of(findings))
    assert has_error != valid


# ---------------------------------------------------------------------------
# ProcessBase integration
# ---------------------------------------------------------------------------


def test_process_exposes_rt(tmp_path, monkeypatch):
    from pympacds.process import ProcessBase

    monkeypatch.setattr(realtime, "apply_in_process", lambda cfg, log=None: {})
    ini = tmp_path / "svc.ini"
    ini.write_text("[DEFAULT]\nloglevel = WARN\n[rt]\nsched_policy = other\n")

    svc = ProcessBase("t", "0.1.0")
    assert svc.setup(["-c", str(ini)]) is True
    assert svc.rt is not None
    assert "sched_policy" in svc.rt


def test_process_rt_none_without_section(tmp_path):
    from pympacds.process import ProcessBase

    ini = tmp_path / "svc.ini"
    ini.write_text("[DEFAULT]\nloglevel = WARN\n")

    svc = ProcessBase("t", "0.1.0")
    assert svc.setup(["-c", str(ini)]) is True
    assert svc.rt is None


def test_process_rt_invalid_is_non_fatal(tmp_path, monkeypatch):
    from pympacds.process import ProcessBase

    monkeypatch.setattr(realtime, "apply_in_process", lambda cfg, log=None: {})
    ini = tmp_path / "svc.ini"
    ini.write_text("[DEFAULT]\nloglevel = WARN\n[rt]\nnice = 999\n")

    svc = ProcessBase("t", "0.1.0")
    assert svc.setup(["-c", str(ini)]) is True  # never fatal
    assert svc.rt is not None


def test_health_status_dict_includes_rt(tmp_path, monkeypatch):
    from pympacds.process import ProcessBase

    monkeypatch.setattr(realtime, "apply_in_process", lambda cfg, log=None: {})

    class Svc(ProcessBase):
        def dbus_health_get_uptime(self) -> int:
            return 1

    ini = tmp_path / "svc.ini"
    ini.write_text("[DEFAULT]\nloglevel = WARN\n[rt]\nsched_policy = other\n")
    svc = Svc("t", "0.1.0")
    svc.setup(["-c", str(ini)])
    payload = svc.health_status_dict()
    assert "rt" in payload
    assert payload["rt"]["ok"] in (True, False)


# ---------------------------------------------------------------------------
# syscall plumbing (stubbed libc)
# ---------------------------------------------------------------------------


class _FakeLibc:
    def __init__(self, rc):
        self._rc = rc
        self.calls = []

    def mlockall(self, flags):
        self.calls.append(("mlockall", flags))
        return self._rc

    def syscall(self, *args):
        self.calls.append(("syscall", args))
        return self._rc


class _FakeLibcWrapper:
    """Fake libc that *does* export the sched_*/ioprio_* wrappers (glibc >= 2.41)."""

    def __init__(self, rc):
        self._rc = rc
        self.calls = []

    def mlockall(self, flags):
        self.calls.append(("mlockall", flags))
        return self._rc

    def sched_setattr(self, pid, attr, flags):
        self.calls.append(("sched_setattr", flags))
        return self._rc

    def sched_getattr(self, pid, attr, size, flags):
        self.calls.append(("sched_getattr", size, flags))
        return self._rc

    def ioprio_get(self, which, who):
        self.calls.append(("ioprio_get", which, who))
        return self._rc

    def syscall(self, *args):  # pragma: no cover - must not be used when wrapper exists
        self.calls.append(("syscall", args))
        return self._rc


def test_mlockall_success(monkeypatch):
    import logging

    fake = _FakeLibc(0)
    monkeypatch.setattr(realtime, "_libc", lambda: fake)
    assert realtime._mlockall(logging.getLogger("t")) is True


def test_mlockall_failure(monkeypatch):
    import logging

    fake = _FakeLibc(-1)
    monkeypatch.setattr(realtime, "_libc", lambda: fake)
    assert realtime._mlockall(logging.getLogger("t")) is False


def test_set_deadline_success(monkeypatch):
    import logging

    fake = _FakeLibc(0)
    monkeypatch.setattr(realtime, "_libc", lambda: fake)
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: 314)
    cfg, _ = realtime.load_rt(
        make_cp(
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    assert realtime._set_deadline(cfg, logging.getLogger("t")) is True


def test_set_deadline_failure_resets(monkeypatch):
    import logging

    fake = _FakeLibc(-1)
    monkeypatch.setattr(realtime, "_libc", lambda: fake)
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: 314)
    monkeypatch.setattr(realtime, "_reset_other", lambda log: None)
    cfg, _ = realtime.load_rt(
        make_cp(
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    assert realtime._set_deadline(cfg, logging.getLogger("t")) is False


def test_set_deadline_unsupported(monkeypatch):
    import logging

    monkeypatch.setattr(realtime, "_libc", lambda: _FakeLibc(0))  # no wrapper
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    cfg, _ = realtime.load_rt(
        make_cp(
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    assert realtime._set_deadline(cfg, logging.getLogger("t")) is False


def test_read_deadline_unsupported(monkeypatch):
    monkeypatch.setattr(realtime, "_libc", lambda: _FakeLibc(0))  # no wrapper
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    assert realtime._read_deadline() is None


def test_set_deadline_uses_libc_wrapper(monkeypatch):
    import logging

    monkeypatch.setattr(realtime, "_libc", lambda: _FakeLibcWrapper(0))
    # the syscall fallback must not be consulted at all
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    cfg, _ = realtime.load_rt(
        make_cp(
            sched_policy="deadline",
            sched_runtime_ns=200_000,
            sched_deadline_ns=1_000_000,
            sched_period_ns=5_000_000,
        )
    )
    assert realtime._set_deadline(cfg, logging.getLogger("t")) is True


def test_read_deadline_uses_libc_wrapper(monkeypatch):
    monkeypatch.setattr(realtime, "_libc", lambda: _FakeLibcWrapper(0))
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    assert realtime._read_deadline() is not None


def test_ioprio_uses_libc_wrapper(monkeypatch):
    monkeypatch.setattr(realtime, "_libc", lambda: _FakeLibcWrapper(0))
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    assert realtime._read_ioprio() == ("best-effort", 0)


@pytest.mark.parametrize(
    "machine,key",
    [
        ("x86_64", "x86_64"),
        ("AMD64", "x86_64"),
        ("aarch64", "aarch64"),
        ("arm64", "aarch64"),
        ("armv7l", "arm"),
        ("armv6l", "arm"),
        ("i686", "i386"),
        ("riscv64", "riscv64"),
        ("loongarch64", "loongarch64"),  # unknown -> treated as unsupported
    ],
)
def test_normalize_machine(machine, key):
    assert realtime._normalize_machine(machine) == key


def test_read_deadline_success(monkeypatch):
    fake = _FakeLibc(0)
    monkeypatch.setattr(realtime, "_libc", lambda: fake)
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: 315)
    info = realtime._read_deadline()
    assert info is not None
    assert "runtime" in info


def test_reset_other_no_crash():
    import logging

    realtime._reset_other(logging.getLogger("t"))


def test_jsonable_set():
    assert realtime._jsonable({2, 1}) == [1, 2]
    assert realtime._jsonable(5) == 5

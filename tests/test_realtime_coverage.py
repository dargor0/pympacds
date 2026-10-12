"""Extra branch coverage for pympacds.realtime (REQ-RT-001 .. REQ-RT-012)."""

import configparser
import os
import resource
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pympacds import realtime  # noqa: E402


def cp(**rt) -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    c.add_section("rt")
    for k, v in rt.items():
        c.set("rt", k, str(v))
    return c


# ---------------------------------------------------------------------------
# validation error branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,key",
    [
        ({"sched_policy": "bogus"}, "rt.sched_policy"),
        (
            {"sched_policy": "fifo", "sched_priority": 10, "sched_runtime_ns": 5},
            "rt.sched_runtime_ns",
        ),
        (
            {
                "sched_policy": "deadline",
                "sched_priority": 5,
                "sched_runtime_ns": 1,
                "sched_deadline_ns": 2,
                "sched_period_ns": 3,
            },
            "rt.sched_priority",
        ),
        ({"sched_policy": "other", "sched_priority": 3}, "rt.sched_priority"),
        ({"sched_policy": "batch", "sched_period_ns": 4}, "rt.sched_period_ns"),
        ({"io_sched_class": "bogus"}, "rt.io_sched_class"),
        ({"io_sched_class": "best-effort", "io_sched_priority": 9}, "rt.io_sched_priority"),
        ({"memlock": "nope"}, "rt.memlock"),
        ({"cpu_affinity": "x"}, "rt.cpu_affinity"),
        ({"oom_score_adjust": 2000}, "rt.oom_score_adjust"),
        ({"timer_slack_ns": -5}, "rt.timer_slack_ns"),
        ({"sched_reset_on_fork": "maybe"}, "rt.sched_reset_on_fork"),
    ],
)
def test_validation_errors(kwargs, key):
    _cfg, findings = realtime.load_rt(cp(**kwargs))
    assert any(f.key == key for f in realtime.errors_of(findings))


def test_idle_nice_warning():
    _cfg, findings = realtime.load_rt(cp(sched_policy="idle", nice=-3))
    assert any(f.severity == realtime.WARNING for f in findings)


# ---------------------------------------------------------------------------
# systemd emission branches
# ---------------------------------------------------------------------------


def test_emit_batch_and_extras():
    cfg, _ = realtime.load_rt(
        cp(
            sched_policy="batch",
            cpu_affinity="2,3",
            io_sched_class="best-effort",
            io_sched_priority=4,
            memlock="infinity",
            oom_score_adjust=-100,
            timer_slack_ns=50000,
        )
    )
    d = realtime.emit_systemd(cfg)
    assert "CPUSchedulingPolicy=batch" in d
    assert "CPUAffinity=2,3" in d
    assert "IOSchedulingClass=best-effort" in d
    assert "IOSchedulingPriority=4" in d
    assert "LimitMEMLOCK=infinity" in d
    assert "OOMScoreAdjust=-100" in d
    assert "TimerSlackNSec=50000" in d


def test_emit_reset_on_fork_fifo():
    cfg, _ = realtime.load_rt(cp(sched_policy="fifo", sched_priority=5, sched_reset_on_fork="true"))
    assert "CPUSchedulingResetOnFork=yes" in realtime.emit_systemd(cfg)


def test_emit_io_class_without_priority():
    cfg, _ = realtime.load_rt(cp(io_sched_class="idle"))
    d = realtime.emit_systemd(cfg)
    assert "IOSchedulingClass=idle" in d
    assert not any(x.startswith("IOSchedulingPriority=") for x in d)


def test_emit_deadline_reset_on_fork():
    cfg, _ = realtime.load_rt(
        cp(
            sched_policy="deadline",
            sched_runtime_ns=1,
            sched_deadline_ns=2,
            sched_period_ns=5,
            sched_reset_on_fork="true",
        )
    )
    d = realtime.emit_systemd(cfg)
    assert "AmbientCapabilities=CAP_SYS_NICE" in d
    assert not any(x.startswith("CPUSchedulingResetOnFork") for x in d)


# ---------------------------------------------------------------------------
# read-backs
# ---------------------------------------------------------------------------


def test_readbacks_values():
    vmlck = realtime._read_vmlck()
    assert vmlck is None or vmlck >= 0
    oom = realtime._read_oom_score_adj()
    assert oom is None or isinstance(oom, int)
    assert realtime._read_timer_slack() is None or realtime._read_timer_slack() >= 0
    assert realtime._read_memlock() is None or realtime._read_memlock() >= 0


class _NoWrapperLibc:
    def syscall(self, *a):  # pragma: no cover - must not be called
        raise AssertionError("syscall should not be called")


class _IoprioLibc:
    def syscall(self, *a):
        return (3 << 13) | 5  # class idle, priority 5


def test_read_ioprio_unsupported(monkeypatch):
    monkeypatch.setattr(realtime, "_libc", lambda: _NoWrapperLibc())
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: None)
    assert realtime._read_ioprio() is None


def test_read_ioprio_syscall_fallback(monkeypatch):
    monkeypatch.setattr(realtime, "_libc", lambda: _IoprioLibc())
    monkeypatch.setattr(realtime, "_syscall_number", lambda name: 252)
    assert realtime._read_ioprio() == ("idle", 5)


# ---------------------------------------------------------------------------
# status object / matchers
# ---------------------------------------------------------------------------


def test_status_all_levers_ok(monkeypatch):
    fake = {
        "nice": -5,
        "sched_policy": "fifo",
        "sched_priority": 10,
        "sched_reset_on_fork": {},
        "cpu_affinity": [0, 1],
        "memlock": resource.RLIM_INFINITY,
        "mlockall": 1234,
        "oom_score_adjust": -100,
        "timer_slack_ns": 50000,
        "io_sched_class": ("best-effort", 4),
    }
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(
        cp(
            sched_policy="fifo",
            sched_priority=10,
            nice=-5,
            cpu_affinity="0-1",
            memlock="infinity",
            mlockall="true",
            oom_score_adjust=-100,
            timer_slack_ns=50000,
            io_sched_class="best-effort",
            io_sched_priority=4,
        )
    )
    status = realtime.status_object(cfg, [], applied={"mlockall": True})
    assert status["ok"] is True
    assert status["cpu_affinity"]["ok"] is True
    assert status["mlockall"]["applied"] is True
    assert status["io_sched_class"]["ok"] is True


def test_status_deadline_levers(monkeypatch):
    fake = {
        "nice": 0,
        "sched_policy": "deadline",
        "sched_priority": 0,
        "sched_reset_on_fork": {"runtime": 1, "deadline": 2, "period": 5, "flags": 1, "policy": 6},
        "cpu_affinity": None,
        "memlock": None,
        "mlockall": None,
        "oom_score_adjust": 0,
        "timer_slack_ns": None,
        "io_sched_class": None,
    }
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(
        cp(
            sched_policy="deadline",
            sched_runtime_ns=1,
            sched_deadline_ns=2,
            sched_period_ns=5,
            sched_reset_on_fork="true",
        )
    )
    status = realtime.status_object(cfg, [])
    assert status["sched_reset_on_fork"]["ok"] is True
    assert status["sched_runtime_ns"]["ok"] is True
    assert status["sched_deadline_ns"]["ok"] is True
    assert status["sched_period_ns"]["ok"] is True


def test_status_memlock_bytes(monkeypatch):
    fake = {"memlock": 2_000_000, "sched_policy": "other", "nice": 0}
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(cp(memlock="1000000"))
    status = realtime.status_object(cfg, [])
    assert status["memlock"]["ok"] is True


def test_status_io_unsupported(monkeypatch):
    fake = {"io_sched_class": None, "sched_policy": "other", "nice": 0}
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(cp(io_sched_class="idle"))
    status = realtime.status_object(cfg, [])
    assert status["io_sched_class"]["ok"] is True


def test_status_levers_unverifiable(monkeypatch):
    fake = {
        "nice": 0,
        "sched_policy": "other",
        "sched_priority": 0,
        "sched_reset_on_fork": {},
        "cpu_affinity": None,
        "memlock": None,
        "mlockall": None,
        "oom_score_adjust": 0,
        "timer_slack_ns": None,
        "io_sched_class": None,
    }
    monkeypatch.setattr(realtime, "verify", lambda: fake)
    cfg, _ = realtime.load_rt(
        cp(
            cpu_affinity="0",
            memlock="infinity",
            mlockall="true",
            timer_slack_ns=1000,
            io_sched_class="best-effort",
            io_sched_priority=3,
        )
    )
    status = realtime.status_object(cfg, [])
    assert status["cpu_affinity"]["ok"] is True
    assert status["memlock"]["ok"] is True
    assert status["mlockall"]["ok"] is True
    assert status["timer_slack_ns"]["ok"] is True
    assert status["io_sched_priority"]["ok"] is True


def test_read_deadline_failure(monkeypatch):
    class _Lib:
        def syscall(self, *a):
            return -1

    monkeypatch.setattr(realtime, "_libc", lambda: _Lib())
    monkeypatch.setattr(realtime, "_syscall_number", lambda n: 315)
    assert realtime._read_deadline() is None

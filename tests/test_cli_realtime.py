"""Tests for the CLI RT integration (REQ-RT-004 strict admin path)."""

import configparser
import io
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pympacds import (
    cli,  # noqa: E402
    realtime,  # noqa: E402
)


def write_ini(tmp_path, body: str) -> str:
    p = tmp_path / "svc.ini"
    p.write_text(body)
    return str(p)


def test_validate_rt_flags_errors():
    cp = configparser.ConfigParser()
    cp.add_section("rt")
    cp.set("rt", "sched_policy", "fifo")  # missing priority
    errors = cli._validate_rt(cp)
    assert any("sched_priority" in e for e in errors)


def test_validate_rt_accepts_valid_and_absent():
    cp = configparser.ConfigParser()
    assert cli._validate_rt(cp) == []
    cp.add_section("rt")
    cp.set("rt", "sched_policy", "fifo")
    cp.set("rt", "sched_priority", "10")
    assert cli._validate_rt(cp) == []


def test_install_realtime_no_section_returns_false(tmp_path):
    config = write_ini(tmp_path, "[DEFAULT]\nloglevel = WARN\n")
    args = SimpleNamespace(config=config, dry_run=True)
    assert cli._install_realtime("test_svc", args, dry_run=True) is False


def test_install_realtime_valid_dry_run(tmp_path, capsys):
    config = write_ini(
        tmp_path,
        "[rt]\nsched_policy = fifo\nsched_priority = 10\n",
    )
    args = SimpleNamespace(config=config, dry_run=True)
    failed = cli._install_realtime("test_svc", args, dry_run=True)
    out = capsys.readouterr().out
    assert failed is False
    assert "Would write" in out
    assert "CPUSchedulingPolicy=fifo" in out


def test_install_realtime_invalid_disables_and_reports(tmp_path, capsys):
    config = write_ini(
        tmp_path,
        "[rt]\nsched_policy = fifo\n",  # missing priority -> error
    )
    args = SimpleNamespace(config=config, dry_run=True)
    failed = cli._install_realtime("test_svc", args, dry_run=True)
    err = capsys.readouterr().err
    assert failed is True
    assert "RT is DISABLED for pympacds-test_svc.service" in err
    assert "rt.sched_priority" in err


def test_report_rt_disabled_message(capsys):
    from pympacds.realtime import Finding

    cli._report_rt_disabled("pympacds-demo.service", [Finding("rt.nice", "oops")])
    err = capsys.readouterr().err
    assert "[rt] found but invalid" in err
    assert "rt.nice: oops" in err


def test_systemd_analyze_unavailable(monkeypatch):
    def raise_fnf(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(cli.subprocess, "run", raise_fnf)
    assert cli._systemd_analyze_verify("x.service") is None


def test_systemd_analyze_ok(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr="")
    )
    assert cli._systemd_analyze_verify("x.service") is True


def test_systemd_analyze_fail(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom")
    )
    assert cli._systemd_analyze_verify("x.service") is False
    assert "boom" in capsys.readouterr().err


def _rt_cfg(**rt):
    cp = configparser.ConfigParser()
    cp.add_section("rt")
    for k, v in rt.items():
        cp.set("rt", k, str(v))
    return realtime.load_rt(cp, environ={})[0]


def test_write_rt_dropin_ok(monkeypatch):
    monkeypatch.setattr(cli.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_systemd_analyze_verify", lambda unit: True)
    written = {}
    real_open = open

    def fake_open(path, mode="r", *a, **k):
        if str(path).endswith("rt.conf"):
            written["path"] = str(path)
            return io.StringIO()
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    cfg = _rt_cfg(sched_policy="fifo", sched_priority=10)
    failed = cli._write_rt_dropin(
        "pympacds-x.service", "/tmp/x/rt.conf", "/tmp/x", cfg, dry_run=False
    )
    assert failed is False
    assert written["path"].endswith("rt.conf")


def test_write_rt_dropin_verify_fail(monkeypatch):
    monkeypatch.setattr(cli.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_systemd_analyze_verify", lambda unit: False)
    monkeypatch.setattr(cli.os.path, "exists", lambda p: True)
    monkeypatch.setattr(cli.os, "remove", lambda p: None)
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO())
    cfg = _rt_cfg(sched_policy="fifo", sched_priority=10)
    assert (
        cli._write_rt_dropin("pympacds-x.service", "/tmp/x/rt.conf", "/tmp/x", cfg, dry_run=False)
        is True
    )


def test_remove_dropin_removes(monkeypatch):
    monkeypatch.setattr(cli.os.path, "exists", lambda p: True)
    removed = []
    monkeypatch.setattr(cli.os, "remove", lambda p: removed.append(p))
    cli._remove_dropin("/x/rt.conf", dry_run=False, announce=True)
    assert removed == ["/x/rt.conf"]


def test_read_service_rt_default_path(monkeypatch):
    monkeypatch.setattr(cli.os.path, "exists", lambda p: False)
    cfg, findings = cli._read_service_rt("svc", SimpleNamespace(config=""))
    assert cfg is None
    assert findings == []


def test_cmd_install_rt_error_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)
    d = tmp_path / "share"
    d.mkdir()
    (d / "pympacds-svc.service").write_text("x")
    monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(d / fn)))
    monkeypatch.setattr("pympacds.cli.shutil.copy", lambda *a: None)
    monkeypatch.setattr("pympacds.cli._run", lambda *a, **k: type("R", (), {"returncode": 0})())
    cfg = tmp_path / "svc.ini"
    cfg.write_text("[rt]\nsched_policy = fifo\n")  # invalid: missing priority
    monkeypatch.setattr(cli.os.path, "exists", lambda p: str(p) == str(cfg))

    class Args:
        service_name = "svc"
        dry_run = False
        no_enable = True
        start = False
        config = str(cfg)

    with pytest.raises(SystemExit):
        cli._cmd_install(Args)


def test_validate_default_errors():
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {
        "loglevel": "BAD",
        "logstdout": "notbool",
        "logmaxsize": "0",
        "logmaxcount": "-1",
    }
    errs = cli._validate_default(cp)
    assert any("loglevel" in e for e in errs)
    assert any("logstdout" in e for e in errs)
    assert any("logmaxsize" in e for e in errs)
    assert any("logmaxcount" in e for e in errs)


def test_validate_dbus_errors():
    cp = configparser.ConfigParser()
    cp["dbus"] = {
        "bus_type": "bogus",
        "drain_timeout_ms": "-1",
        "contract_health": "notbool",
        "heartbeat_interval_s": "1",
        "discovery_enabled": "nope",
    }
    errs = cli._validate_dbus(cp)
    assert any("bus_type" in e for e in errs)
    assert any("drain_timeout_ms" in e for e in errs)
    assert any("contract_health" in e for e in errs)
    assert any("heartbeat_interval_s" in e for e in errs)
    assert any("discovery_enabled" in e for e in errs)

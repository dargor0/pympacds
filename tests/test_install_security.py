"""Install-time security checks (REQ-SEC-002..013).

All tests run unprivileged (REQ-SEC-011): the permission predicate is exercised
with **fabricated** ``os.stat_result`` objects and with **real** file metadata
(existing root-owned system files, and files/dirs the runner can create and
``chmod``). Only the genuinely root-requiring operations are skipped elsewhere.
"""

import configparser
import os
import shutil
import stat
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pympacds import security  # noqa: E402


def _fake_stat(entries):
    """Build ``stat_of(path)`` from ``{path: (mode, uid)}``."""

    def _stat(path):
        if path not in entries:
            raise FileNotFoundError(path)
        mode, uid = entries[path]
        return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))

    return _stat


def _identity(path):
    return path


def _dir(mode=0o755, uid=0):
    return (stat.S_IFDIR | mode, uid)


def _file(mode=0o755, uid=0):
    return (stat.S_IFREG | mode, uid)


def _current_user() -> str:
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def _current_group() -> str:
    import grp

    return grp.getgrgid(os.getgid()).gr_name


class TestPredicateFabricated:
    def test_accept_root_owned(self):
        entries = {
            "/usr/local/bin/app": _file(0o755, 0),
            "/usr/local/bin": _dir(),
            "/usr/local": _dir(),
            "/usr": _dir(),
            "/": _dir(),
        }
        assert (
            security.check_exec_target(
                "/usr/local/bin/app",
                "pympacds-user",
                resolve=_identity,
                stat_of=_fake_stat(entries),
            )
            == []
        )

    def test_accept_user_owned(self):
        uid = os.getuid()
        entries = {
            "/opt/app": _file(0o700, uid),
            "/opt": _dir(0o700, uid),
            "/": _dir(),
        }
        assert (
            security.check_exec_target(
                "/opt/app", _current_user(), resolve=_identity, stat_of=_fake_stat(entries)
            )
            == []
        )

    def test_reject_group_other_writable_file(self):
        entries = {
            "/usr/local/bin/app": _file(0o777, 0),
            "/usr/local/bin": _dir(),
            "/usr/local": _dir(),
            "/usr": _dir(),
            "/": _dir(),
        }
        problems = security.check_exec_target(
            "/usr/local/bin/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat(entries)
        )
        assert any("group/other-writable" in p for p in problems)

    def test_reject_foreign_owner(self):
        entries = {
            "/opt/app": _file(0o755, 1000),
            "/opt": _dir(),
            "/": _dir(),
        }
        problems = security.check_exec_target(
            "/opt/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat(entries)
        )
        assert any("owned by uid 1000" in p for p in problems)

    def test_reject_world_writable_parent_including_sticky(self):
        entries = {
            "/tmp/app": _file(0o755, 0),
            "/tmp": _dir(0o1777, 0),
            "/": _dir(),
        }
        problems = security.check_exec_target(
            "/tmp/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat(entries)
        )
        assert any(p.startswith("/tmp:") and "group/other-writable" in p for p in problems)

    def test_reject_non_executable(self):
        entries = {
            "/opt/app": _file(0o644, 0),
            "/opt": _dir(),
            "/": _dir(),
        }
        problems = security.check_exec_target(
            "/opt/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat(entries)
        )
        assert any("not executable" in p for p in problems)

    def test_reject_non_regular(self):
        entries = {
            "/opt/app": _dir(),
            "/opt": _dir(),
            "/": _dir(),
        }
        problems = security.check_exec_target(
            "/opt/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat(entries)
        )
        assert problems == ["/opt/app: not a regular file"]

    def test_missing(self):
        problems = security.check_exec_target(
            "/nope/app", "pympacds-user", resolve=_identity, stat_of=_fake_stat({})
        )
        assert problems and "not found" in problems[0]

    def test_config_does_not_require_executable_bit(self):
        entries = {
            "/etc/pympacds/app.ini": _file(0o644, 0),
            "/etc/pympacds": _dir(),
            "/etc": _dir(),
            "/": _dir(),
        }
        assert (
            security.check_config_target(
                "/etc/pympacds/app.ini",
                "pympacds-user",
                resolve=_identity,
                stat_of=_fake_stat(entries),
            )
            == []
        )


class TestPredicateRealMetadata:
    def test_accept_real_root_owned_system_executable(self):
        exe = shutil.which("true") or "/usr/bin/true"
        if not os.path.exists(exe):
            pytest.skip("no system executable available")
        assert security.check_exec_target(exe, "pympacds-user") == []

    def test_reject_real_user_owned_file(self, tmp_path):
        prog = tmp_path / "app"
        prog.write_text("#!/bin/sh\n")
        prog.chmod(0o755)
        assert security.check_exec_target(str(prog), "pympacds-user")

    def test_reject_real_group_other_writable_file(self, tmp_path):
        prog = tmp_path / "app"
        prog.write_text("x")
        prog.chmod(0o777)
        problems = security.check_exec_target(str(prog), "pympacds-user")
        assert any("group/other-writable" in p for p in problems)

    def test_reject_real_symlink_to_unsafe_target(self, tmp_path):
        target = tmp_path / "real"
        target.write_text("x")
        target.chmod(0o755)
        link = tmp_path / "link"
        link.symlink_to(target)
        problems = security.check_exec_target(str(link), "pympacds-user")
        assert any(str(target) in p for p in problems)


class TestRootGate:
    def test_alert_and_dry_run(self, capsys):
        assert security.confirm_root("svc", dry_run=True) is True
        assert "ROOT" in capsys.readouterr().err

    def test_non_tty_aborts(self, capsys):
        assert security.confirm_root("svc", isatty=lambda: False) is False
        assert "refusing" in capsys.readouterr().err

    def test_interactive_yes_proceeds(self):
        assert security.confirm_root("svc", isatty=lambda: True, input_fn=lambda p: "yes") is True

    def test_interactive_other_aborts(self):
        assert security.confirm_root("svc", isatty=lambda: True, input_fn=lambda p: "nope") is False

    def test_eof_aborts(self):
        def _boom(prompt):
            raise EOFError

        assert security.confirm_root("svc", isatty=lambda: True, input_fn=_boom) is False

    def test_is_root_user(self):
        assert security.is_root_user("root") is True
        assert security.is_root_user("0") is True
        assert security.is_root_user("pympacds-user") is False
        assert security.uid_for_user(None) is None


class TestHardening:
    def test_baseline_contains_expected_and_excludes_risky(self):
        body = security.render_hardening_dropin()
        assert body.startswith("[Service]\n")
        for directive in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "PrivateTmp=yes",
            "ProtectProc=invisible",
            "SystemCallArchitectures=native",
            "UMask=0027",
        ):
            assert directive in body
        for excluded in (
            "PrivateDevices=yes",
            "ProcSubset=pid",
            "MemoryDenyWriteExecute=yes",
            "RestrictAddressFamilies=",
            "CapabilityBoundingSet=",
        ):
            assert excluded not in body

    def test_directive_categories(self):
        assert "User=pympacds-user" in security.ALWAYS_DIRECTIVES
        assert "Group=pympacds-user" in security.ALWAYS_DIRECTIVES
        assert "NoNewPrivileges=yes" in security.STRICT_DIRECTIVES
        assert "ProtectSystem=strict" in security.STRICT_DIRECTIVES
        for directive in security.INCOMPATIBLE_DIRECTIVES:
            assert directive not in security.STRICT_DIRECTIVES
        body = security.render_hardening_dropin()
        for directive in security.STRICT_DIRECTIVES:
            assert directive in body
        for directive in security.INCOMPATIBLE_DIRECTIVES:
            assert directive not in body

    def test_rt_deadline_omits_nonewprivs(self):
        body = security.render_hardening_dropin(rt_deadline=True)
        assert "NoNewPrivileges=yes" not in body
        assert "ProtectSystem=strict" in body

    def test_user_dropin(self):
        assert security.render_user_dropin("pympacds-user") == (
            "[Service]\nUser=pympacds-user\nGroup=pympacds-user\n"
        )

    def test_user_dropin_with_supplementary_groups(self):
        assert security.render_user_dropin("pympacds-user", supplementary_groups="gpio") == (
            "[Service]\nUser=pympacds-user\nGroup=pympacds-user\nSupplementaryGroups=gpio\n"
        )


class TestIdentityValidation:
    def test_valid_root_user(self):
        assert security.validate_service_user("root") == []

    def test_valid_numeric_root_user(self):
        assert security.validate_service_user("0") == []

    def test_invalid_user_injection_rejected(self):
        assert security.validate_service_user("root\nExecStart=/bin/sh")

    def test_unknown_user_rejected(self):
        assert security.validate_service_user("definitely-no-such-user-xyz")

    def test_supplementary_groups_normalized_and_deduplicated(self):
        group = _current_group()
        normalized, problems = security.parse_supplementary_groups(f"{group}, {group}")
        assert normalized == group
        assert problems == []

    def test_unknown_supplementary_group_rejected(self):
        normalized, problems = security.parse_supplementary_groups("definitely-no-such-group-xyz")
        assert normalized == ""
        assert problems

    def test_supplementary_group_injection_rejected(self):
        _, problems = security.parse_supplementary_groups("root\nUser=root")
        assert any("User=root" in problem for problem in problems)

    def test_sensitive_supplementary_groups(self):
        assert security.sensitive_supplementary_groups("root disk gpio") == ["root", "disk"]


class TestInstallCli:
    def test_refuses_user_owned_program(self, tmp_path, monkeypatch):
        from pympacds import cli

        # This test targets the path check; bypass the (unrelated) user-existence
        # check, since `pympacds-user` may not exist in the test environment.
        monkeypatch.setattr("pympacds.security.validate_service_user", lambda *a, **k: [])
        prog = tmp_path / "app"
        prog.write_text("x")
        prog.chmod(0o755)
        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program=str(prog),
            user="pympacds-user",
            config="",
            strict=True,
        )
        with pytest.raises(SystemExit):
            cli._cmd_install(args)

    def test_accepts_root_owned_program_dry_run(self, tmp_path, monkeypatch, capsys):
        from pympacds import cli

        current = _current_user()
        program = shutil.which("true") or "/usr/bin/true"
        tpl = tmp_path / "pympacds@.service.in"
        tpl.write_text("User=@USER@\nExecStart=@EXEC_START@ -c /etc/pympacds/%i.ini\n")
        monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(tpl)))

        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program=program,
            user=current,
            config="",
            strict=True,
        )
        cli._cmd_install(args)
        out = capsys.readouterr().out
        assert f"User={current}" in out
        assert "Would write:" in out
        assert "ProtectSystem=strict" in out

    def test_no_strict_packaged_unit_is_pre_hardened(self, tmp_path, monkeypatch, capsys):
        from pympacds import cli

        monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: "/nonexistent"))

        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program="",
            user=None,
            config="",
            strict=False,
        )
        cli._cmd_install(args)
        assert "pre-hardened" in capsys.readouterr().err

    def test_supp_groups_dropin_preview(self, tmp_path, monkeypatch, capsys):
        from pympacds import cli

        current = _current_user()
        current_group = _current_group()
        program = shutil.which("true") or "/usr/bin/true"
        tpl = tmp_path / "pympacds@.service.in"
        tpl.write_text("User=@USER@\nExecStart=@EXEC_START@ -c /etc/pympacds/%i.ini\n")
        monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(tpl)))
        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program=program,
            user=current,
            config="",
            strict=True,
            supp_groups=current_group,
        )
        cli._cmd_install(args)
        assert f"SupplementaryGroups={current_group}" in capsys.readouterr().out

    def test_supp_groups_unknown_rejected(self, tmp_path):
        from pympacds import cli

        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program="",
            user=None,
            config="",
            strict=True,
            supp_groups="definitely-no-such-group-xyz",
        )
        with pytest.raises(SystemExit):
            cli._cmd_install(args)

    def test_supp_groups_malformed_rejected(self):
        from pympacds import cli

        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program="",
            user=None,
            config="",
            strict=True,
            supp_groups="gpio\nUser=root",
        )
        with pytest.raises(SystemExit):
            cli._cmd_install(args)

    def test_supp_groups_sensitive_warns(self, tmp_path, monkeypatch, capsys):
        from pympacds import cli

        current = _current_user()
        program = shutil.which("true") or "/usr/bin/true"
        tpl = tmp_path / "pympacds@.service.in"
        tpl.write_text("User=@USER@\nExecStart=@EXEC_START@ -c /etc/pympacds/%i.ini\n")
        monkeypatch.setattr("pympacds.cli._find_resource", staticmethod(lambda fn: str(tpl)))
        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program=program,
            user=current,
            config="",
            strict=True,
            supp_groups="root",
        )
        cli._cmd_install(args)
        captured = capsys.readouterr()
        assert "high-risk" in captured.err
        assert "SupplementaryGroups=root" in captured.out

    def test_invalid_user_rejected(self):
        from pympacds import cli

        args = SimpleNamespace(
            service_name="svc",
            dry_run=True,
            program="",
            user="root\nExecStart=/bin/sh",
            config="",
            strict=True,
            supp_groups=None,
        )
        with pytest.raises(SystemExit):
            cli._cmd_install(args)

    def test_validate_reports_permission_errors(self, tmp_path, capsys):
        from pympacds import cli

        prog = tmp_path / "app"
        prog.write_text("x")
        prog.chmod(0o755)
        cp = configparser.ConfigParser()
        args = SimpleNamespace(schema=None, user="pympacds-user", program=str(prog), config="")
        with pytest.raises(SystemExit):
            cli._cmd_config_validate(cp, args)
        assert "must be root" in capsys.readouterr().err

    def test_validate_safe_program_ok(self, capsys):
        from pympacds import cli

        program = shutil.which("true") or "/usr/bin/true"
        cp = configparser.ConfigParser()
        args = SimpleNamespace(schema=None, user="pympacds-user", program=program, config="")
        cli._cmd_config_validate(cp, args)
        assert "valid" in capsys.readouterr().out.lower()


class TestShippedUnits:
    """The shipped units carry the strict baseline (REQ-SEC-008/009/013)."""

    _SERVICES = ("mqtt", "http", "gpio")

    def _unit(self, service: str) -> str:
        root = os.path.join(os.path.dirname(__file__), "..")
        path = os.path.join(root, "services", service, "debian", f"pympacds-{service}.service")
        with open(path) as f:
            return f.read()

    @pytest.mark.parametrize("service", _SERVICES)
    def test_unit_runs_as_service_user_with_baseline(self, service):
        text = self._unit(service)
        assert "User=pympacds-user" in text
        assert "Group=pympacds-user" in text
        assert "ProtectSystem=strict" in text
        assert "NoNewPrivileges=yes" in text
        assert "RemoveIPC=yes" in text

    def test_gpio_uses_group_and_no_device_directives(self):
        text = self._unit("gpio")
        assert "SupplementaryGroups=gpio" in text
        assert "RestrictAddressFamilies=AF_UNIX" in text
        assert "PrivateDevices=" not in text
        assert "DevicePolicy=" not in text

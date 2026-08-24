"""Test _cmd_install using monkeypatched system boundaries."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestCmdInstall:
    def test_install_dry_run(self, tmp_path, monkeypatch):
        """_cmd_install --dry-run locates files without root."""
        from pympacds.cli import _cmd_install

        src_dir = tmp_path / "share"
        src_dir.mkdir()
        svc_unit = src_dir / "pympacds-test_svc.service"
        svc_conf = src_dir / "test_svc.conf"
        svc_unit.write_text("[Unit]")
        svc_conf.write_text("<busconfig/>")

        monkeypatch.setattr(
            "pympacds.cli._find_resource",
            staticmethod(lambda fn: str(src_dir / fn)),
        )

        class Args:
            service_name = "test_svc"
            dry_run = True

        _cmd_install(Args)

    def test_install_full(self, tmp_path, monkeypatch):
        """_cmd_install simulates full install with mocked system calls."""
        from pympacds.cli import _cmd_install

        monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)

        src_dir = tmp_path / "share"
        src_dir.mkdir()
        svc_unit = src_dir / "pympacds-test_svc.service"
        svc_unit.write_text("[Unit]")

        monkeypatch.setattr(
            "pympacds.cli._find_resource",
            staticmethod(lambda fn: str(src_dir / fn)),
        )

        calls = []

        def fake_run(cmd, check=True, quiet=False):
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr("pympacds.cli._run", fake_run)
        monkeypatch.setattr("pympacds.cli.shutil.copy", lambda *a: None)
        monkeypatch.setattr("pympacds.cli.os.path.exists", lambda p: False)

        class Args:
            service_name = "test_svc"
            dry_run = False
            no_enable = False
            start = True

        _cmd_install(Args)
        cmd_strs = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in cmd_strs)
        assert any("enable" in s for s in cmd_strs)
        assert any("start" in s for s in cmd_strs)

    def test_install_no_enable(self, tmp_path, monkeypatch):
        """_cmd_install with --no-enable skips enable and start."""
        from pympacds.cli import _cmd_install

        monkeypatch.setattr("pympacds.cli._ensure_root", lambda: None)

        src_dir = tmp_path / "share"
        src_dir.mkdir()
        svc_unit = src_dir / "pympacds-test_svc.service"
        svc_unit.write_text("[Unit]")

        monkeypatch.setattr(
            "pympacds.cli._find_resource",
            staticmethod(lambda fn: str(src_dir / fn)),
        )

        calls = []

        def fake_run(cmd, check=True, quiet=False):
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr("pympacds.cli._run", fake_run)
        monkeypatch.setattr("pympacds.cli.shutil.copy", lambda *a: None)
        monkeypatch.setattr("pympacds.cli.os.path.exists", lambda p: False)

        class Args:
            service_name = "test_svc"
            dry_run = False
            no_enable = True
            start = False

        _cmd_install(Args)
        cmd_strs = [" ".join(c) for c in calls]
        assert not any("enable" in s for s in cmd_strs)
        assert not any("start" in s for s in cmd_strs)

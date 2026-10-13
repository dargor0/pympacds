"""Shared fixtures for pympacds-gpio tests (REQ-GPIO-009)."""

import os
import shlex
import signal
import subprocess
import tempfile
import time

import pytest
from helpers import FakeShim

# ------------------------------------------------------------------
# Implementation-agnostic black-box harness (REQ-XCUT-002.1).
#   MPACDS_BUS_PREFIX   bus-name prefix (default "org.pympacds")
#   MPACDS_CMD_<NAME>   launch command (else the Python module launcher)
# ------------------------------------------------------------------

BUS_PREFIX = os.environ.get("MPACDS_BUS_PREFIX", "org.pympacds")

# Make the service and core importable by subprocesses even when they are not
# pip-installed, mirroring the pytest "pythonpath" setting (service src + core src).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SOURCE_PATHS = os.pathsep.join(
    [
        os.path.join(_HERE, "..", "src"),
        os.path.join(_HERE, "..", "..", "..", "src"),
    ]
)


def service_command(name, default_argv):
    override = os.environ.get(f"MPACDS_CMD_{name.upper()}")
    if override:
        return shlex.split(override)
    return list(default_argv)


@pytest.fixture(scope="session")
def bus_prefix():
    return BUS_PREFIX


@pytest.fixture(scope="session")
def launch_service():
    def _launch(name, ini, address, default_argv, extra_env=None):
        env = os.environ.copy()
        env["DBUS_SYSTEM_BUS_ADDRESS"] = address
        existing = env.get("PYTHONPATH")
        if existing:
            env["PYTHONPATH"] = f"{_SOURCE_PATHS}{os.pathsep}{existing}"
        else:
            env["PYTHONPATH"] = _SOURCE_PATHS
        if extra_env:
            env.update(extra_env)
        argv = service_command(name, default_argv) + ["-c", str(ini)]
        return subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )

    return _launch


@pytest.fixture
def fake_shim():
    return FakeShim()


@pytest.fixture(scope="session")
def session_bus_address():
    """Spawn an isolated D-Bus daemon for black-box tests (REQ-SVC-009)."""
    tmpdir = tempfile.mkdtemp(prefix="pympacds_gpio_dbus_")
    socket_path = os.path.join(tmpdir, "bus")
    config_path = os.path.join(tmpdir, "session.conf")
    config_xml = f"""\
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
    <type>system</type>
    <keep_umask/>
    <allow_anonymous/>
    <listen>unix:path={socket_path}</listen>
    <policy context="default">
        <allow user="*"/>
        <allow own="*"/>
        <allow send_destination="*"/>
        <allow send_interface="*"/>
        <allow eavesdrop="true"/>
    </policy>
</busconfig>
"""
    with open(config_path, "w") as f:
        f.write(config_xml)

    stdout_path = os.path.join(tmpdir, "daemon.out")
    stderr_path = os.path.join(tmpdir, "daemon.err")
    stdout_f = open(stdout_path, "w")
    stderr_f = open(stderr_path, "w")

    proc = subprocess.Popen(
        ["dbus-daemon", "--config-file", config_path, "--print-address", "--nofork", "--nosyslog"],
        stdout=stdout_f,
        stderr=stderr_f,
        text=True,
    )

    time.sleep(0.3)
    stdout_f.flush()
    stderr_f.flush()

    with open(stdout_path) as f:
        stdout_text = f.read()

    printed_address = ""
    for line in stdout_text.splitlines():
        line = line.strip()
        if line and "unix:" in line:
            printed_address = line
            break

    if proc.poll() is not None:
        pytest.fail(f"dbus-daemon exited early with code {proc.returncode}")
    if not printed_address:
        pytest.fail("dbus-daemon produced no address")

    yield printed_address

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    stdout_f.close()
    stderr_f.close()

    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)

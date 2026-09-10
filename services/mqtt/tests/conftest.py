"""Shared fixtures for pympacds-mqtt tests (REQ-MQTT-008)."""

import os
import signal
import subprocess
import tempfile
import time

import pytest
from helpers import FakeClient


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture(scope="session")
def session_bus_address():
    """Spawn an isolated D-Bus daemon for black-box tests (REQ-SVC-009)."""
    tmpdir = tempfile.mkdtemp(prefix="pympacds_mqtt_dbus_")
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

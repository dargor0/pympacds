"""Shared pytest fixtures for pympacds tests."""

import os
import signal
import subprocess
import tempfile
import time
import configparser
import pytest


# ------------------------------------------------------------------
# INI config fixtures
# ------------------------------------------------------------------

@pytest.fixture
def ini_file():
    """Create a temporary INI config file with defaults."""
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {
        "loglevel": "WARN",
        "logstdout": "false",
        "logfile": "",
    }
    cp["dbus"] = {
        "bus_type": "system",
        "bus_prefix": "org.pympacds.test",
        "contract_health": "true",
        "contract_metrics": "false",
        "contract_lifecycle": "false",
        "contract_config": "false",
        "discovery_enabled": "false",
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False
    ) as f:
        cp.write(f)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def process_base(ini_file):
    """Create a configured ProcessBase instance."""
    from pympacds.process import ProcessBase

    p = ProcessBase("test_svc", "0.1.0", "pytest service")
    p.setup(["-c", ini_file])
    return p


# ------------------------------------------------------------------
# D-Bus session bus fixture for integration tests.
# Requires: dbus-daemon installed.
# ------------------------------------------------------------------


@pytest.fixture(scope="session")
def session_bus_address():
    """Spawn an isolated D-Bus session daemon for the test session."""

    tmpdir = tempfile.mkdtemp(prefix="pympacds_dbus_")
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

    # read the address line (--print-address with --nofork prints it then blocks)
    stdout_path = os.path.join(tmpdir, "daemon.out")
    stderr_path = os.path.join(tmpdir, "daemon.err")
    stdout_f = open(stdout_path, "w")
    stderr_f = open(stderr_path, "w")

    proc = subprocess.Popen(
        [
            "dbus-daemon",
            "--config-file", config_path,
            "--print-address",
            "--nofork",
            "--nosyslog",
        ],
        stdout=stdout_f,
        stderr=stderr_f,
        text=True,
    )

    # wait for the address to be written
    time.sleep(0.3)
    stdout_f.flush()
    stderr_f.flush()

    with open(stdout_path) as f:
        stdout_text = f.read()

    # parse address from first line of stdout
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
    
    with open(stdout_path) as f:
        stdout_text = f.read()
    
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
async def session_bus(session_bus_address):
    """Create a connected DBusManager on the isolated session bus."""
    import logging
    from pympacds.dbus import DBusManager

    logger = logging.getLogger("test_dbus")
    logger.setLevel(logging.WARN)
    
    oldaddr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", None)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = session_bus_address

    mgr = DBusManager(
        logger=logger,
        busname="test.fixture",
        bus_prefix="org.pympacds",
        discovery_enabled=False,
        name_flags=0,  # NameFlag.NONE — test bus has no existing name to replace
    )

    await mgr.start()
    yield mgr
    await mgr.stop()
    
    if oldaddr:
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = oldaddr
    else:
        os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)

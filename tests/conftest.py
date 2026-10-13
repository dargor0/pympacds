"""Shared pytest fixtures for pympacds tests."""

import configparser
import os
import shlex
import signal
import subprocess
import tempfile
import time

import pytest

# ------------------------------------------------------------------
# Implementation-agnostic black-box harness (REQ-XCUT-002.1).
#
# Wire-contract tests launch a service as an external process and talk to it
# only over D-Bus, so the same tests drive the Python reference implementation
# or the C++ port unchanged.  Two environment variables parameterize them:
#   MPACDS_BUS_PREFIX   bus-name prefix (default "org.pympacds")
#   MPACDS_CMD_<NAME>   launch command for service <NAME> (else the default)
# ------------------------------------------------------------------

BUS_PREFIX = os.environ.get("MPACDS_BUS_PREFIX", "org.pympacds")


def service_command(name, default_argv):
    """Return the argv used to launch service ``name``.

    Overridable with ``MPACDS_CMD_<NAME>`` (e.g.
    ``MPACDS_CMD_MQTT=/usr/bin/cppmpacds-mqtt``) so the C++ implementation can
    be driven by the same tests.
    """
    override = os.environ.get(f"MPACDS_CMD_{name.upper()}")
    if override:
        return shlex.split(override)
    return list(default_argv)


@pytest.fixture(scope="session")
def bus_prefix():
    """Bus-name prefix shared by the service under test and the D-Bus client."""
    return BUS_PREFIX


@pytest.fixture(scope="session")
def launch_service():
    """Return ``launch(name, ini, address, default_argv, extra_env=None)``.

    Starts the service under test as a subprocess connected to the isolated
    test bus.  ``name`` selects the ``MPACDS_CMD_<NAME>`` override; otherwise
    ``default_argv`` is used.  ``-c <ini>`` is appended to the command.
    """

    def _launch(name, ini, address, default_argv, extra_env=None):
        env = os.environ.copy()
        env["DBUS_SYSTEM_BUS_ADDRESS"] = address
        if extra_env:
            env.update(extra_env)
        argv = service_command(name, default_argv) + ["-c", str(ini)]
        return subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    return _launch


# ------------------------------------------------------------------
# INI config fixtures
# ------------------------------------------------------------------


@pytest.fixture
def ini_file(bus_prefix):
    """Create a temporary INI config file with defaults."""
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {
        "loglevel": "WARN",
        "logstdout": "false",
        "logfile": "",
    }
    cp["dbus"] = {
        "bus_type": "system",
        "bus_prefix": f"{bus_prefix}.test",
        "contract_health": "true",
        "contract_metrics": "false",
        "contract_lifecycle": "false",
        "contract_config": "false",
        "discovery_enabled": "false",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
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


@pytest.fixture
def fake_dbus_backend(monkeypatch):
    """Monkeypatch the D-Bus library getters so DBusManager needs no real bus.

    ``DBusManager.__init__`` resolves the backend through the module-level
    ``get_dbus_lib``/``get_dbus_aio``/``get_dbus_service`` names in
    ``pympacds.dbus``.  Replacing them with in-memory fakes lets unit tests
    construct a manager without a running event loop or a live socket.
    """
    import pympacds.dbus as dbus_mod

    class FakeLib:
        class BusType:
            SYSTEM = 1
            SESSION = 2

        class NameFlag:
            REPLACE_EXISTING = 1

        class Message:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    class FakeMessageBus:
        def __init__(self, bus_type=None, **kwargs):
            self.bus_type = bus_type

    class FakeAio:
        MessageBus = FakeMessageBus

    class FakeSvc:
        pass

    monkeypatch.setattr(dbus_mod, "get_dbus_lib", lambda: FakeLib())
    monkeypatch.setattr(dbus_mod, "get_dbus_aio", lambda: FakeAio())
    monkeypatch.setattr(dbus_mod, "get_dbus_service", lambda: FakeSvc())


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
            "--config-file",
            config_path,
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
async def session_bus(session_bus_address, bus_prefix):
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
        bus_prefix=bus_prefix,
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

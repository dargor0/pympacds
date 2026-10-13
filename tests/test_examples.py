"""Integration test: sensor + collector communicating via friendbus.

Black-box (REQ-XCUT-002.1): the example services are launched as external
processes and are exercised only over D-Bus and their log output, so the same
tests drive the Python reference implementation or the C++ port — the latter by
setting ``MPACDS_CMD_SENSOR`` / ``MPACDS_CMD_COLLECTOR`` and
``MPACDS_BUS_PREFIX``.
"""

import os
import signal
import sys
import time

_EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
_PKG_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _argv(name):
    """Default (Python) launcher for an example service."""
    return [sys.executable, os.path.join(_EXAMPLES, f"{name}.py")]


def _env():
    return {"PYTHONPATH": os.pathsep.join([_PKG_SRC, _EXAMPLES])}


class TestSensorCollector:
    def test_sensor_starts_and_emits(
        self, session_bus_address, tmp_path, launch_service, bus_prefix
    ):
        """Sensor starts, connects to D-Bus, emits measurements."""
        ini = tmp_path / "sensor.ini"
        ini.write_text(
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            f"[dbus]\nbus_prefix = {bus_prefix}.test\ndiscovery_enabled = true\n"
            "[sensor]\nperiod_s = 1\n"
        )

        proc = launch_service("sensor", ini, session_bus_address, _argv("sensor"), _env())
        time.sleep(2)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=3)

        assert "Sensor started" in stderr, f"stdout: {stdout}\nstderr: {stderr}"
        assert "Emitted measurement" in stderr

    def test_collector_discovers_sensor(
        self, session_bus_address, tmp_path, launch_service, bus_prefix
    ):
        """Collector discovers a running sensor via friendbus and receives
        measurements."""
        sensor_ini = tmp_path / "sensor.ini"
        collector_ini = tmp_path / "collector.ini"
        ini_content = (
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            f"[dbus]\nbus_prefix = {bus_prefix}.test\ndiscovery_enabled = true\n"
        )
        sensor_ini.write_text(ini_content + "[sensor]\nperiod_s = 1\n")
        collector_ini.write_text(ini_content)

        # start sensor first
        sensor = launch_service("sensor", sensor_ini, session_bus_address, _argv("sensor"), _env())
        time.sleep(1)

        # start collector
        collector = launch_service(
            "collector", collector_ini, session_bus_address, _argv("collector"), _env()
        )
        time.sleep(3)

        # stop both
        collector.send_signal(signal.SIGTERM)
        sensor.send_signal(signal.SIGTERM)

        cout, cerr = collector.communicate(timeout=5)
        sout, serr = sensor.communicate(timeout=5)

        assert "Collector started" in cerr, f"collector stdout: {cout}\nstderr: {cerr}"
        assert "Subscribed to sensor" in cerr, f"stdout: {cout}\n sensor stderr: {serr}"
        assert "Received measurement" in cerr, f"stdout: {cout}\nstderr: {cerr}"

    def test_collector_notices_sensor_disconnect(
        self, session_bus_address, tmp_path, launch_service, bus_prefix
    ):
        """Collector detects when a sensor disappears from the bus."""
        sensor_ini = tmp_path / "sensor.ini"
        collector_ini = tmp_path / "collector.ini"
        ini_content = (
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            f"[dbus]\nbus_prefix = {bus_prefix}.test\ndiscovery_enabled = true\n"
        )
        sensor_ini.write_text(ini_content + "[sensor]\nperiod_s = 1\n")
        collector_ini.write_text(ini_content)

        sensor = launch_service("sensor", sensor_ini, session_bus_address, _argv("sensor"), _env())
        time.sleep(1)

        collector = launch_service(
            "collector", collector_ini, session_bus_address, _argv("collector"), _env()
        )
        time.sleep(2)

        # kill sensor
        sensor.send_signal(signal.SIGTERM)
        sensor.communicate(timeout=3)

        # give collector time to notice (its scan interval is 5 s)
        time.sleep(7)

        collector.send_signal(signal.SIGTERM)
        cout, cerr = collector.communicate(timeout=5)

        assert "Unsubscribed from sensor" in cerr, f"stdout: {cout}\nstderr: {cerr}"

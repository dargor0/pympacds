"""Integration test: sensor + collector communicating via friendbus."""

import os
import signal
import subprocess
import sys
import time

_EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
_PKG_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _run_service(script, ini, address, extra_env=None):
    """Launch an example service as a subprocess connected to the test bus."""
    env = os.environ.copy()
    env["DBUS_SYSTEM_BUS_ADDRESS"] = address
    env["PYTHONPATH"] = _PKG_SRC
    if extra_env:
        env.update(extra_env)

    return subprocess.Popen(
        [sys.executable, os.path.join(_EXAMPLES, script), "-c", ini],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


class TestSensorCollector:
    def test_sensor_starts_and_emits(self, session_bus_address, tmp_path):
        """Sensor starts, connects to D-Bus, emits measurements."""
        ini = tmp_path / "sensor.ini"
        ini.write_text(
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            "[dbus]\nbus_prefix = org.pympacds.test\ndiscovery_enabled = true\n"
            "[sensor]\nperiod_s = 1\n"
        )

        proc = _run_service("sensor.py", str(ini), session_bus_address)
        time.sleep(2)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=3)

        assert "Sensor started" in stderr, f"stdout: {stdout}\nstderr: {stderr}"
        assert "Emitted measurement" in stderr

    def test_collector_discovers_sensor(self, session_bus_address, tmp_path):
        """Collector discovers a running sensor via friendbus and receives
        measurements."""
        sensor_ini = tmp_path / "sensor.ini"
        collector_ini = tmp_path / "collector.ini"
        ini_content = (
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            "[dbus]\nbus_prefix = org.pympacds.test\ndiscovery_enabled = true\n"
        )
        sensor_ini.write_text(ini_content + "[sensor]\nperiod_s = 1\n")
        collector_ini.write_text(ini_content)

        # start sensor first
        sensor = _run_service("sensor.py", str(sensor_ini), session_bus_address)
        time.sleep(1)

        # start collector
        collector = _run_service("collector.py", str(collector_ini), session_bus_address)
        time.sleep(3)

        # stop both
        collector.send_signal(signal.SIGTERM)
        sensor.send_signal(signal.SIGTERM)

        cout, cerr = collector.communicate(timeout=5)
        sout, serr = sensor.communicate(timeout=5)

        assert "Collector started" in cerr, f"collector stdout: {cout}\nstderr: {cerr}"
        assert "Subscribed to sensor" in cerr, f"stdout: {cout}\n sensor stderr: {serr}"
        assert "Received measurement" in cerr, f"stdout: {cout}\nstderr: {cerr}"

    def test_collector_notices_sensor_disconnect(self, session_bus_address, tmp_path):
        """Collector detects when a sensor disappears from the bus."""
        sensor_ini = tmp_path / "sensor.ini"
        collector_ini = tmp_path / "collector.ini"
        ini_content = (
            "[DEFAULT]\nloglevel = INFO\nlogstdout = true\nlogfile =\n"
            "[dbus]\nbus_prefix = org.pympacds.test\ndiscovery_enabled = true\n"
        )
        sensor_ini.write_text(ini_content + "[sensor]\nperiod_s = 1\n")
        collector_ini.write_text(ini_content)

        sensor = _run_service("sensor.py", str(sensor_ini), session_bus_address)
        time.sleep(1)

        collector = _run_service("collector.py", str(collector_ini), session_bus_address)
        time.sleep(2)

        # kill sensor
        sensor.send_signal(signal.SIGTERM)
        sensor.communicate(timeout=3)

        # give collector time to notice (its scan interval is 5 s)
        time.sleep(7)

        collector.send_signal(signal.SIGTERM)
        cout, cerr = collector.communicate(timeout=5)

        assert "Unsubscribed from sensor" in cerr, f"stdout: {cout}\nstderr: {cerr}"

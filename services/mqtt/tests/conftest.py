"""Shared fixtures for pympacds-mqtt tests (REQ-MQTT-008)."""

import pytest
from helpers import FakeClient


@pytest.fixture
def fake_client():
    return FakeClient()

"""Contract unit tests (REQ-GPIO-005)."""

import pytest

from pympacds_gpio.contracts import GpioContract


class FakeBase:
    def __init__(self):
        self.calls = []

    def dbus_gpio_get_value(self, line):
        self.calls.append(("get_value", line))
        return True

    def dbus_gpio_set_value(self, line, value):
        self.calls.append(("set_value", line, value))
        return True

    def dbus_gpio_get_default_value(self, line):
        self.calls.append(("get_default_value", line))
        return False

    def dbus_gpio_set_default_value(self, line, value):
        self.calls.append(("set_default_value", line, value))
        return True

    def dbus_gpio_get_lines(self):
        self.calls.append(("get_lines",))
        return "{}"

    def dbus_gpio_status(self):
        self.calls.append(("status",))
        return "{}"

    def dbus_gpio_watched_lines(self):
        self.calls.append(("watched_lines",))
        return ["btn"]


@pytest.fixture
def contract():
    base = FakeBase()
    return GpioContract("org.pympacds.GPIO", base), base


def test_contract_construction_requires_callbacks():
    class EmptyBase:
        pass

    with pytest.raises(AttributeError):
        GpioContract("org.pympacds.GPIO", EmptyBase())


def test_get_value_delegates(contract):
    c, base = contract
    c.get_value("0")
    assert base.calls == [("get_value", "0")]


def test_set_value_delegates(contract):
    c, base = contract
    c.set_value("relay", True)
    assert base.calls == [("set_value", "relay", True)]


def test_get_default_value_delegates(contract):
    c, base = contract
    c.get_default_value("relay")
    assert base.calls == [("get_default_value", "relay")]


def test_set_default_value_delegates(contract):
    c, base = contract
    c.set_default_value("relay", False)
    assert base.calls == [("set_default_value", "relay", False)]


def test_get_lines_delegates(contract):
    c, base = contract
    c.get_lines()
    assert base.calls == [("get_lines",)]


def test_status_delegates(contract):
    c, base = contract
    c.status()
    assert base.calls == [("status",)]


def test_line_changed_signal_returns_args(contract):
    c, _ = contract
    assert c.line_changed("btn", 0, True) == ["btn", 0, True]


def test_watched_lines_property_delegates(contract):
    c, base = contract
    assert c.watched_lines == ["btn"]
    assert base.calls == [("watched_lines",)]


def test_contract_metadata():
    assert GpioContract.iface_name == "GPIO"
    assert "gpio" in GpioContract.iface_provides

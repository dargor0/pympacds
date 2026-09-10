"""Tests for ServiceContract and decorators (REQ-IFACE-001 through REQ-IFACE-009)."""

import pytest


class TestServiceContract:
    def test_construction(self):
        from pympacds.contracts import ServiceContract

        class MockBase:
            def dbus_ping(self):
                pass

        class MyContract(ServiceContract):
            iface_name = "My"

            def __init__(self, ifname, base):
                super().__init__(ifname, base)
                self._require("dbus_ping")

        obj = MyContract("com.example.My", MockBase())
        assert obj.iface_name == "My"
        assert obj.iface_version == "1.0.0"

    def test_require_missing_raises(self):
        from pympacds.contracts import ServiceContract

        class MockBase:
            pass

        with pytest.raises(AttributeError, match="requires method"):

            class BadContract(ServiceContract):
                def __init__(self, ifname, base):
                    super().__init__(ifname, base)
                    self._require("missing_method")

            BadContract("x", MockBase())

    def test_require_not_callable_raises(self):
        from pympacds.contracts import ServiceContract

        class MockBase:
            not_a_method = 42

        with pytest.raises(AttributeError, match="requires callable"):

            class BadContract(ServiceContract):
                def __init__(self, ifname, base):
                    super().__init__(ifname, base)
                    self._require("not_a_method")

            BadContract("x", MockBase())

    def test_contract_metadata(self):
        from pympacds.contracts import ServiceContract

        class MyContract(ServiceContract):
            iface_name = "My"
            iface_version = "2.0.0"
            iface_provides = ["health"]
            iface_requires = ["network"]

        meta = MyContract._get_contract_metadata()
        assert meta["iface_name"] == "My"
        assert meta["iface_version"] == "2.0.0"
        assert meta["provides"] == ["health"]
        assert meta["requires"] == ["network"]

    def test_inherits_from_library_service_interface(self):
        from pympacds.contracts import ServiceContract
        from pympacds import get_dbus_service

        svc_iface = get_dbus_service().ServiceInterface
        assert issubclass(ServiceContract, svc_iface)


class TestDecorators:
    def test_dbus_method_exists(self):
        from pympacds.contracts import dbus_method

        assert callable(dbus_method)

    def test_dbus_signal_exists(self):
        from pympacds.contracts import dbus_signal

        assert callable(dbus_signal)

    def test_dbus_property_exists(self):
        from pympacds.contracts import dbus_property

        assert callable(dbus_property)


"""Additional tests for contracts decorators."""

import asyncio
import pytest


class TestDBusMethodDecorator:
    def test_timeout_parameter_accepted(self):
        from pympacds.contracts import dbus_method

        # timeout_ms should be accepted for async functions
        @dbus_method(timeout_ms=5000)
        async def handler(self, arg: "s") -> "s":
            return arg

        assert hasattr(handler, "_pympacds_timeout_ms")
        assert handler._pympacds_timeout_ms == 5000

    def test_timeout_ignored_for_sync(self):
        from pympacds.contracts import dbus_method

        @dbus_method(timeout_ms=5000)
        def handler(self, arg: "s") -> "s":
            return arg

        # timeout_ms should be silently ignored for sync handlers
        assert not hasattr(handler, "_pympacds_timeout_ms")

    def test_name_override(self):
        from pympacds.contracts import dbus_method

        @dbus_method(name="custom_name")
        def original_name(self) -> "b":
            return True

        assert original_name.__dict__.get("__DBUS_METHOD") is not None


class TestDBusSignalDecorator:
    def test_signal_decorator(self):
        from pympacds.contracts import dbus_signal

        @dbus_signal()
        def my_signal(self, val: "s") -> "s":
            return [val]

        assert my_signal.__dict__.get("__DBUS_SIGNAL") is not None


class TestDBusPropertyDecorator:
    def test_property_decorator(self):
        from pympacds.contracts import dbus_property

        class PropTest:
            @dbus_property()
            def value(self) -> "u":
                return 42

        pt = PropTest()
        assert pt.value == 42


"""Tests for built-in contracts (REQ-XCUT-004).

Contract methods are dispatched by the D-Bus library's message bus,
not by direct Python calls. These tests verify construction, metadata,
and error detection — not runtime delegation.
"""

import pytest


class MockService:
    """Minimal mock implementing all contract callbacks."""

    def dbus_health_ping(self):
        return True

    def dbus_health_status(self):
        return '{"ok":true}'

    def dbus_health_get_uptime(self):
        return 42

    def dbus_health_get_provides(self):
        return ["health"]

    def dbus_health_get_requires(self):
        return []

    def dbus_lifecycle_restart(self):
        return True

    def dbus_lifecycle_shutdown(self):
        return True

    def dbus_config_get(self, s, k):
        return "val"

    def dbus_config_set(self, s, k, v):
        return True

    def dbus_metrics_get(self):
        return "{}"


class TestHealthContract:
    def test_construction(self):
        from pympacds.builtin_contracts import HealthContract

        hc = HealthContract("com.example.Health", MockService())
        assert hc.iface_name == "Health"
        assert hc.iface_version == "1.0.0"
        assert hc.iface_provides == ["health"]

    def test_uptime_property_readable(self):
        from pympacds.builtin_contracts import HealthContract

        svc = MockService()
        hc = HealthContract("com.example.Health", svc)
        assert getattr(hc, "uptime") == 42

    def test_missing_callback_raises(self):
        from pympacds.builtin_contracts import HealthContract

        with pytest.raises(AttributeError, match="requires method"):
            HealthContract("x", object())


class TestLifecycleContract:
    def test_construction(self):
        from pympacds.builtin_contracts import LifecycleContract

        lc = LifecycleContract("com.example.Life", MockService())
        assert lc.iface_name == "Lifecycle"
        assert lc.iface_provides == ["lifecycle"]


class TestConfigContract:
    def test_construction(self):
        from pympacds.builtin_contracts import ConfigContract

        cc = ConfigContract("com.example.Config", MockService())
        assert cc.iface_name == "Config"
        assert cc.iface_provides == ["config"]


class TestMetricsContract:
    def test_construction(self):
        from pympacds.builtin_contracts import MetricsContract

        mc = MetricsContract("com.example.Metrics", MockService())
        assert mc.iface_name == "Metrics"
        assert mc.iface_provides == ["metrics"]


class TestContractCapabilityTags:
    """The capability-tag class attributes must remain plain lists
    (REQ-SVC-014). They are named ``iface_provides``/``iface_requires`` to avoid
    colliding with the ``provides``/``requires`` ``@dbus_property`` members on
    ``HealthContract``."""

    def test_provides_and_requires_are_lists(self):
        from pympacds.builtin_contracts import (
            HealthContract,
            LifecycleContract,
            ConfigContract,
            MetricsContract,
        )

        for cls in (
            HealthContract,
            LifecycleContract,
            ConfigContract,
            MetricsContract,
        ):
            assert isinstance(cls.iface_provides, list), (
                f"{cls.__name__}.iface_provides is not a list "
                f"(got {type(cls.iface_provides).__name__})"
            )
            assert isinstance(cls.iface_requires, list), (
                f"{cls.__name__}.iface_requires is not a list "
                f"(got {type(cls.iface_requires).__name__})"
            )

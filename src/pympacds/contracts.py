"""ServiceContract base class and D-Bus method/signal/property decorators."""

import inspect
import logging

from . import get_dbus_service

_logger = logging.getLogger(__name__)

_SvcInterface = get_dbus_service().ServiceInterface


class ServiceContract(_SvcInterface):
    """Base class for D-Bus interface contracts.

    Inherits from the underlying D-Bus library's ``ServiceInterface``.
    Contracts delegate business logic to ``self.base`` (the
    ``ProcessBase`` subclass instance).

    Args:
        ifname: The D-Bus interface name.
        base: The host service object implementing the required callbacks.
    """

    iface_name: str = ""
    iface_version: str = "1.0.0"
    iface_provides: list[str] = []
    iface_requires: list[str] = []

    def __init__(self, ifname: str, base):
        super().__init__(ifname)
        self.base = base

    def _require(self, *method_names: str) -> None:
        """Validate that the base object provides all required callbacks.

        Raises:
            AttributeError: If any required method is missing or not callable.
        """
        for mname in method_names:
            mref = getattr(self.base, mname, None)
            if mref is None:
                raise AttributeError(
                    f"ServiceContract '{self.iface_name}' requires method "
                    f"'{mname}' in the base object."
                )
            if not callable(mref):
                raise AttributeError(
                    f"ServiceContract '{self.iface_name}' requires callable "
                    f"'{mname}' in the base object."
                )

    @classmethod
    def _get_contract_metadata(cls) -> dict:
        """Return metadata for discovery."""
        return {
            "iface_name": cls.iface_name,
            "iface_version": cls.iface_version,
            "provides": list(cls.iface_provides),
            "requires": list(cls.iface_requires),
        }


# ------------------------------------------------------------------
# custom decorators — wrap the underlying library and add framework
# features (timeout_ms for async handlers, etc.)
# ------------------------------------------------------------------


def dbus_method(
    name: str | None = None,
    disabled: bool = False,
    timeout_ms: int = 0,
):
    """Decorator for D-Bus method handlers.

    Wraps the underlying library's method decorator and adds
    ``timeout_ms`` for async handlers (REQ-DBUS-012).

    Args:
        name: D-Bus member name. Defaults to the Python function name.
        disabled: If True, the method is hidden from introspection.
        timeout_ms: Max execution time for async handlers before
            cancellation. 0 means no timeout. Ignored for sync handlers.
    """

    _svc = get_dbus_service()

    def decorator(fn):
        # apply the underlying library decorator
        decorated = _svc.dbus_method(name=name, disabled=disabled)(fn)

        if inspect.iscoroutinefunction(fn) and timeout_ms > 0:
            original = getattr(decorated, "__wrapped__", decorated)
            setattr(decorated, "_pympacds_timeout_ms", timeout_ms)
            setattr(decorated, "_pympacds_original_fn", original)

        return decorated

    return decorator


def dbus_signal(
    name: str | None = None,
    disabled: bool = False,
):
    """Decorator for D-Bus signal definitions.

    Wraps the underlying library's signal decorator.
    """
    _svc = get_dbus_service()
    return _svc.dbus_signal(name=name, disabled=disabled)


def dbus_property(
    name: str | None = None,
    disabled: bool = False,
    access=None,
):
    """Decorator for D-Bus property definitions.

    Wraps the underlying library's property decorator.
    """
    _svc = get_dbus_service()
    kwargs = {"name": name, "disabled": disabled}
    if access is not None:
        kwargs["access"] = access
    return _svc.dbus_property(**kwargs)

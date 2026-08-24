"""DBusManager — manages the D-Bus bus connection and interface export for a service."""

import asyncio
import logging

from . import get_dbus_lib, get_dbus_aio, get_dbus_service

_DEFAULT_BUS_PREFIX = "org.pympacds"


def _prefix_to_path(prefix: str) -> str:
    """Convert a bus name prefix to an object path root."""
    return "/" + prefix.replace(".", "/")


class DBusManager:
    """Manages the D-Bus connection and interface export for a service.

    Args:
        logger: Logger instance for operational messages.
        busname: Desired bus name (auto-prefixed if needed).
        bus_prefix: Namespace prefix.  Falls back to the ``[dbus] bus_prefix``
            INI value, then ``org.pympacds``.
        bus_type: ``BusType.SYSTEM`` or ``BusType.SESSION``.  Falls back to
            the ``[dbus] bus_type`` INI value, then ``SYSTEM``.
        name_flags: Name request flags.  Falls back to ``REPLACE_EXISTING``.
        drain_timeout_ms: Queue drain timeout in milliseconds before disconnect.
        discovery_enabled: Whether to track friend buses.
    """

    def __init__(
        self,
        logger: logging.Logger,
        busname: str,
        bus_prefix: str | None = None,
        bus_type: int | None = None,
        name_flags: int | None = None,
        drain_timeout_ms: int = 2000,
        discovery_enabled: bool = True,
    ):
        self.logger = logger
        self._lib: object = get_dbus_lib()
        self._aio: object = get_dbus_aio()
        self._svc: object = get_dbus_service()

        self._bus_prefix: str = bus_prefix or _DEFAULT_BUS_PREFIX

        if not busname.startswith(self._bus_prefix):
            items = busname.split(".")
            items.insert(0, self._bus_prefix)
            self.busname = ".".join(items)
        else:
            self.busname = busname

        self._bus_type: int = (
            bus_type if bus_type is not None else self._lib.BusType.SYSTEM
        )
        self._name_flags: int = (
            name_flags
            if name_flags is not None
            else self._lib.NameFlag.REPLACE_EXISTING
        )
        self._drain_timeout_ms: int = drain_timeout_ms
        self._discovery_enabled: bool = discovery_enabled

        self.ifacelist: dict[str, list[object]] = {}
        self.friendbus: set[str] = set()
        self.friendchanges: asyncio.Event = asyncio.Event()

        self.bus: object = self._aio.MessageBus(bus_type=self._bus_type)
        self._dbusif: object | None = None
        self._obj_root: str = _prefix_to_path(self._bus_prefix)

        self.logger.debug(f"DBus ({self.busname}) initialized.")

    # ------------------------------------------------------------------
    # interface registration
    # ------------------------------------------------------------------

    def add_interface(self, pathname: str, iface: object) -> None:
        """Register a D-Bus interface on an object path.

        Args:
            pathname: Relative or absolute object path.  Relative paths are
                expanded to ``{root}/{pathname}``.
            iface: A ``ServiceInterface`` instance (from the D-Bus library)
                or subclass thereof.
        """
        full_path = self._resolve_path(pathname)
        if full_path not in self.ifacelist:
            self.ifacelist[full_path] = []
        self.ifacelist[full_path].append(iface)
        self.logger.debug(
            "DBus (%s) added interface %s to path %s.",
            self.busname,
            iface.name,
            full_path,
        )

    def _resolve_path(self, pathname: str) -> str:
        """Expand a relative path to the full object path."""
        if pathname.startswith("/"):
            return pathname
        return f"{self._obj_root}/{pathname}"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to the bus, request name, export interfaces, scan peers."""
        await self.bus.connect()
        await self.bus.request_name(name=self.busname, flags=self._name_flags)
        for pname, ifacelist in self.ifacelist.items():
            for iface in ifacelist:
                self.bus.export(path=pname, interface=iface)
        if self._discovery_enabled:
            await self.update_friendbus()
        self.logger.debug("DBus (%s) started.", self.busname)

    async def stop(self) -> None:
        """Drain the message queue and disconnect from the bus."""
        self.logger.debug("DBus (%s) stopping.", self.busname)
        for _ in range(self._drain_timeout_ms):
            try:
                empty = len(self.bus._writer.messages) == 0
            except TypeError:
                empty = self.bus._writer.messages.qsize() == 0
            if empty:
                break
            await asyncio.sleep(0.001)
        self.bus.disconnect()
        await self.bus.wait_for_disconnect()
        self.logger.debug("DBus (%s) stopped.", self.busname)

    # ------------------------------------------------------------------
    # remote proxies
    # ------------------------------------------------------------------

    async def get_interface(
        self, busname: str, pathname: str, ifname: str
    ) -> object:
        """Introspect a remote bus and return a proxy interface.

        Args:
            busname: Remote bus name.
            pathname: Object path on the remote bus.
            ifname: D-Bus interface name to obtain.

        Returns:
            A proxy interface object for the requested D-Bus interface.
        """
        introspection = await self.bus.introspect(busname, pathname)
        obj = self.bus.get_proxy_object(busname, pathname, introspection)
        return obj.get_interface(ifname)

    # ------------------------------------------------------------------
    # friend bus discovery
    # ------------------------------------------------------------------

    async def update_friendbus(self) -> None:
        """Scan the bus for peer services sharing the same prefix.

        Reconciles the friend set against the current bus state: names that
        no longer have an owner are removed, new names are added, and the
        ``friendchanges`` event is signaled if anything changed.
        """
        await self._connect_dbusif()
        try:
            retval = await self._dbusif.call_list_names()
            current = {n for n in retval if n.startswith(self._bus_prefix)}
            if current != self.friendbus:
                self.friendbus = current
                self.friendchanges.set()
            self.logger.debug(
                "DBus: updated %d services from %d total.",
                len(self.friendbus),
                len(retval),
            )
        except Exception:
            self.logger.exception(
                "Unable to update friendbus: DBus ListNames exception"
            )

    def query_friend_busname(self, query: str = "") -> list[str]:
        """Return friend bus names, optionally filtered by substring.

        Args:
            query: Substring to match.  Empty string returns all friends.
        """
        if not query:
            return list(self.friendbus)
        return [n for n in self.friendbus if query in n]

    def is_friend_busname(self, query: str) -> bool:
        """Return True if *query* is a known friend bus name."""
        return query in self.friendbus

    async def get_friend_bus(
        self,
        busname: str,
        ifname: str | None = None,
        objpath: str | None = None,
    ) -> object:
        """Obtain a proxy for a discovered friend bus.

        Args:
            busname: Friend bus name (must be in ``friendbus``).
            ifname: Interface name.  Defaults to the first component of
                *busname* split on ``-``.
            objpath: Object path.  Defaults to the namespace root.

        Raises:
            ValueError: If *busname* is not in the friend set.
        """
        if busname not in self.friendbus:
            raise ValueError(f"Busname not available: {busname}")
        if ifname is None:
            ifname = busname.partition("-")[0]
        if objpath is None:
            objpath = self._obj_root
        return await self.get_interface(
            busname=busname, pathname=objpath, ifname=ifname
        )

    async def wait_friend_changes(self) -> None:
        """Await until the friend set changes."""
        self.friendchanges.clear()
        await self.friendchanges.wait()

    # ------------------------------------------------------------------
    # internal: D-Bus daemon proxy
    # ------------------------------------------------------------------

    async def _connect_dbusif(self) -> None:
        """Lazily create a proxy to ``org.freedesktop.DBus`` and subscribe
        to name-ownership signals for friend-bus tracking."""
        if self._dbusif is None:
            self._dbusif = await self.get_interface(
                busname="org.freedesktop.DBus",
                pathname="/org/freedesktop/DBus",
                ifname="org.freedesktop.DBus",
            )
            self._dbusif.on_name_acquired(self._signal_name_acquired)
            self._dbusif.on_name_lost(self._signal_name_lost)
            self._dbusif.on_name_owner_changed(self._signal_name_owner_changed)

    def _signal_name_acquired(self, name: str) -> None:
        """Handle ``NameAcquired`` — add to friend set if the name shares our prefix."""
        if name.startswith(self._bus_prefix):
            self.friendbus.add(name)
            self.logger.debug("Detected new friend service: %s", name)
            self.friendchanges.set()

    def _signal_name_lost(self, name: str) -> None:
        """Handle ``NameLost`` — remove from friend set if the name shares our prefix."""
        if name.startswith(self._bus_prefix):
            self.friendbus.discard(name)
            self.logger.debug("Detected missing friend service: %s", name)
            self.friendchanges.set()

    def _signal_name_owner_changed(
        self, name: str, old_owner: str, new_owner: str
    ) -> None:
        """Handle ``NameOwnerChanged`` — delegate to ``_signal_name_acquired``
        or ``_signal_name_lost`` depending on whether the owner appeared or
        disappeared."""
        if name.startswith(self._bus_prefix):
            if new_owner == "":
                self._signal_name_lost(name)
            elif old_owner == "":
                self._signal_name_acquired(name)

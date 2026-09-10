"""BusIntrospect — D-Bus introspection as native Python data (REQ-DBUS-013)."""

import copy
import xml.etree.ElementTree as ET


class BusIntrospect:
    """Immutable snapshot of the introspection of one D-Bus object path.

    Backed by plain dicts; constructed from the introspection XML produced by
    the D-Bus library (``Node.tostring()``). The standard D-Bus plumbing
    interfaces (``org.freedesktop.DBus.*``) are filtered out.

    Args:
        xml_string: The introspection XML document.
        path: The object path this introspection is for.
    """

    _STD_PREFIX = "org.freedesktop.DBus."

    def __init__(self, xml_string: str, path: str = "/"):
        self._path = path
        self._data = self._parse(xml_string)

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _arg_dict(elem) -> dict:
        return {"name": elem.get("name", ""), "type": elem.get("type", "")}

    @staticmethod
    def _annotations(elem) -> dict:
        return {a.get("name", ""): a.get("value", "") for a in elem if a.tag == "annotation"}

    def _parse(self, xml_string: str) -> dict:
        # Parse a D-Bus introspection document, whose shape is:
        #
        #   <node>
        #     <interface name="...">
        #       <method name="..."> <arg name=.. type=.. direction=..> </method>
        #       <signal name="..."> <arg .../> ... </signal>
        #       <property name="..." type="..." access="..."/>
        #     </interface>
        #     <node name="..."/>      <!-- a child object path -->
        #   </node>
        root = ET.fromstring(xml_string)

        # The result mirrors that tree for the search helpers:
        #   data["interfaces"] = {iface_name: {methods, signals, properties,
        #                                       annotations}}
        #   data["children"]  = [full child object paths ...]
        data: dict = {
            "path": self._path,
            "interfaces": {},
            "children": [],
        }

        for child in root:
            if child.tag == "interface":
                self._parse_interface(child, data)
            elif child.tag == "node":
                self._parse_node(child, data)

        return data

    def _parse_interface(self, elem, data: dict) -> None:
        """Extract one <interface> element into ``data["interfaces"]``."""
        name = elem.get("name", "")
        # Skip the standard D-Bus plumbing interfaces (Introspectable, Peer,
        # Properties) — only the service's own interfaces matter.
        if name.startswith(self._STD_PREFIX):
            return

        iface: dict = {
            "methods": {},
            "signals": {},
            "properties": {},
            "annotations": self._annotations(elem),
        }
        for member in elem:
            if member.tag == "method":
                iface["methods"][member.get("name", "")] = self._parse_method(member)
            elif member.tag == "signal":
                iface["signals"][member.get("name", "")] = self._parse_signal(member)
            elif member.tag == "property":
                iface["properties"][member.get("name", "")] = self._parse_property(member)

        data["interfaces"][name] = iface

    def _parse_node(self, elem, data: dict) -> None:
        """Extract a child <node> element into ``data["children"]``.

        A <node name="..."> element names a child object *relative* to this
        path. Expand it to a full path so introspect_tree() can visit it
        directly without re-deriving the hierarchy.
        """
        cname = elem.get("name")
        if cname:
            base = self._path.rstrip("/")
            data["children"].append(f"{base}/{cname}" if base else f"/{cname}")

    def _parse_method(self, elem) -> dict:
        """Extract a <method> element; args are split by D-Bus "direction"
        ("in" = caller -> service, "out" = service -> caller)."""
        return {
            "in": [
                self._arg_dict(a)
                for a in elem
                if a.tag == "arg" and a.get("direction", "in") == "in"
            ],
            "out": [
                self._arg_dict(a)
                for a in elem
                if a.tag == "arg" and a.get("direction", "in") != "in"
            ],
            "annotations": self._annotations(elem),
        }

    def _parse_signal(self, elem) -> dict:
        """Extract a <signal> element; all args are emitted (outward), so they
        are collected flat as "args" without a direction split."""
        return {
            "args": [self._arg_dict(a) for a in elem if a.tag == "arg"],
            "annotations": self._annotations(elem),
        }

    def _parse_property(self, elem) -> dict:
        """Extract a <property> element."""
        return {
            "type": elem.get("type", ""),
            "access": elem.get("access", "read"),
            "annotations": self._annotations(elem),
        }

    # ------------------------------------------------------------------
    # snapshot access
    # ------------------------------------------------------------------

    def as_dict(self) -> dict:
        """Return a deep copy of the full snapshot (base data is untouched)."""
        return copy.deepcopy(self._data)

    def path(self) -> str:
        """The object path this snapshot is for."""
        return self._path

    def children(self) -> list[str]:
        """Child object paths directly below this node."""
        return list(self._data["children"])

    # ------------------------------------------------------------------
    # search facilities
    # ------------------------------------------------------------------

    def interface_names(self) -> list[str]:
        return list(self._data["interfaces"].keys())

    def get_interface(self, name: str) -> dict | None:
        return self._data["interfaces"].get(name)

    def has_interface(self, name: str) -> bool:
        return name in self._data["interfaces"]

    def _members(self, kind: str, interface=None) -> dict:
        if interface is not None:
            iface = self._data["interfaces"].get(interface)
            return iface[kind] if iface else {}
        result: dict = {}
        for iface in self._data["interfaces"].values():
            result.update(iface[kind])
        return result

    def methods(self, interface=None) -> dict:
        """Methods of one interface (or all interfaces if ``interface`` is None)."""
        return self._members("methods", interface)

    def signals(self, interface=None) -> dict:
        """Signals of one interface (or all interfaces if ``interface`` is None)."""
        return self._members("signals", interface)

    def properties(self, interface=None) -> dict:
        """Properties of one interface (or all interfaces if ``interface`` is None)."""
        return self._members("properties", interface)

    def _find(self, kind: str, name: str) -> list[tuple[str, dict]]:
        found: list[tuple[str, dict]] = []
        for iname, iface in self._data["interfaces"].items():
            if name in iface[kind]:
                found.append((iname, iface[kind][name]))
        return found

    def find_method(self, name: str) -> list[tuple[str, dict]]:
        """Return ``(interface_name, method_dict)`` for every interface
        declaring a method named *name*."""
        return self._find("methods", name)

    def find_signal(self, name: str) -> list[tuple[str, dict]]:
        """Return ``(interface_name, signal_dict)`` for every interface
        declaring a signal named *name*."""
        return self._find("signals", name)

    def find_property(self, name: str) -> list[tuple[str, dict]]:
        """Return ``(interface_name, property_dict)`` for every interface
        declaring a property named *name*."""
        return self._find("properties", name)

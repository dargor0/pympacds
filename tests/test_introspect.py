"""Tests for BusIntrospect (REQ-DBUS-013)."""

import pytest


_XML = """\
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect">
      <arg name="data" type="s" direction="out"/>
    </method>
  </interface>
  <interface name="org.pympacds.HTTP">
    <method name="send">
      <arg name="payload" type="s" direction="in"/>
      <arg name="token" type="s" direction="out"/>
    </method>
    <signal name="request_completed">
      <arg name="token" type="s"/>
      <arg name="result" type="s"/>
    </signal>
    <property name="connected" type="b" access="read"/>
  </interface>
  <node name="stats"/>
</node>
"""


@pytest.fixture
def bi():
    from pympacds.introspect import BusIntrospect

    return BusIntrospect(_XML, path="/org/pympacds/http")


class TestBusIntrospect:
    def test_path(self, bi):
        assert bi.path() == "/org/pympacds/http"

    def test_filters_std_interfaces(self, bi):
        assert bi.interface_names() == ["org.pympacds.HTTP"]
        assert not bi.has_interface("org.freedesktop.DBus.Introspectable")

    def test_find_method(self, bi):
        found = bi.find_method("send")
        assert len(found) == 1
        iface, method = found[0]
        assert iface == "org.pympacds.HTTP"
        assert method["in"] == [{"name": "payload", "type": "s"}]
        assert method["out"] == [{"name": "token", "type": "s"}]

    def test_find_signal(self, bi):
        found = bi.find_signal("request_completed")
        assert len(found) == 1
        iface, signal = found[0]
        assert iface == "org.pympacds.HTTP"
        assert signal["args"] == [
            {"name": "token", "type": "s"},
            {"name": "result", "type": "s"},
        ]

    def test_find_property(self, bi):
        found = bi.find_property("connected")
        assert len(found) == 1
        iface, prop = found[0]
        assert iface == "org.pympacds.HTTP"
        assert prop["type"] == "b"
        assert prop["access"] == "read"

    def test_children(self, bi):
        assert bi.children() == ["/org/pympacds/http/stats"]

    def test_find_missing(self, bi):
        assert bi.find_method("nope") == []
        assert bi.find_signal("nope") == []
        assert bi.find_property("nope") == []

    def test_as_dict_is_a_copy(self, bi):
        d1 = bi.as_dict()
        d1["interfaces"]["org.pympacds.HTTP"]["methods"].clear()
        # base data is unaffected
        assert bi.find_method("send")

    def test_methods_across_interfaces(self):
        from pympacds.introspect import BusIntrospect

        xml = """\
<node>
  <interface name="a">
    <method name="m"/>
  </interface>
  <interface name="b">
    <method name="m"/>
    <method name="n"/>
  </interface>
</node>
"""
        b = BusIntrospect(xml, path="/")
        methods = b.methods()
        assert set(methods.keys()) == {"m", "n"}
        assert set(b.methods(interface="a").keys()) == {"m"}

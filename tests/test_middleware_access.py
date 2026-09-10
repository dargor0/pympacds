"""Tests for ProcessBase.get_middleware() (REQ-MIDW-017)."""

import pytest

from pympacds.middleware import MiddlewareBase, MiddlewareSpec
from pympacds.process import ProcessBase


def _make_process():
    return ProcessBase("t", "1.0")


class TestGetMiddlewarePureLogic:
    def test_none_when_no_middleware(self):
        p = _make_process()
        assert p.get_middleware(MiddlewareBase) is None
        assert p.get_middleware("signal_action") is None

    def test_by_class_exact_match(self):
        p = _make_process()
        mw = MiddlewareBase(p, None)
        p._middleware_instances = [mw]
        p._middleware_names = {}
        assert p.get_middleware(MiddlewareBase) is mw

    def test_subclass_is_not_a_match(self):
        class Sub(MiddlewareBase):
            pass

        p = _make_process()
        mw = MiddlewareBase(p, None)
        p._middleware_instances = [mw]
        p._middleware_names = {}
        assert p.get_middleware(Sub) is None
        assert p.get_middleware(MiddlewareBase) is mw

    def test_by_name(self):
        p = _make_process()
        mw = MiddlewareBase(p, None)
        p._middleware_instances = [mw]
        p._middleware_names = {"foo": mw}
        assert p.get_middleware("foo") is mw
        assert p.get_middleware("bar") is None


class TestGetMiddlewareIntegration:
    def test_programmatic_by_class(self, process_base):
        class MW(MiddlewareBase):
            pass

        process_base.register_middleware = lambda: [MiddlewareSpec(MW, None)]
        process_base._init_middleware()

        assert process_base.get_middleware(MW) is process_base._middleware_instances[0]
        assert process_base.get_middleware("unknown") is None

    def test_config_by_name_and_class(self, process_base, monkeypatch):
        import importlib.metadata

        class MW(MiddlewareBase):
            pass

        class FakeEP:
            name = "fake_mw"

            def load(self):
                return MW

        monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [FakeEP()])
        process_base.config["middleware"] = {"fake_mw": "none"}
        process_base._init_middleware()

        assert process_base.get_middleware("fake_mw") is process_base._middleware_instances[0]
        assert process_base.get_middleware(MW) is process_base._middleware_instances[0]

    def test_singleton_programmatic_plus_config(self, process_base, monkeypatch):
        import importlib.metadata

        class MW(MiddlewareBase):
            pass

        class FakeEP:
            name = "fake_mw"

            def load(self):
                return MW

        monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [FakeEP()])
        process_base.register_middleware = lambda: [MiddlewareSpec(MW, None)]
        process_base.config["middleware"] = {"fake_mw": "cfg_section"}
        process_base.config["cfg_section"] = {"k": "v"}
        process_base._init_middleware()

        assert len(process_base._middleware_instances) == 1
        mw = process_base._middleware_instances[0]
        # singleton: the config entry updated the programmatic instance
        assert mw.section == "cfg_section"
        assert process_base.get_middleware(MW) is mw
        assert process_base.get_middleware("fake_mw") is mw

    @pytest.mark.asyncio
    async def test_failed_setup_removed_from_lookup(self, process_base, monkeypatch):
        import importlib.metadata

        class FailingMW(MiddlewareBase):
            async def setup(self):
                raise ValueError("boom")

            def on_error(self, error):
                return True  # continue (mark failed, skip)

        class FakeEP:
            name = "failing_mw"

            def load(self):
                return FailingMW

        monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [FakeEP()])
        process_base.config["middleware"] = {"failing_mw": "none"}
        process_base._init_middleware()

        assert process_base.get_middleware("failing_mw") is not None
        assert process_base.get_middleware(FailingMW) is not None

        await process_base._setup_middleware()

        assert process_base.get_middleware("failing_mw") is None
        assert process_base.get_middleware(FailingMW) is None

"""Tests for utils.py (REQ-UTILS)."""

from pympacds.utils import get_systemhw_data


def test_get_systemhw_data_serial():
    result = get_systemhw_data("serial")
    assert isinstance(result, str)


def test_get_systemhw_data_model():
    result = get_systemhw_data("model")
    assert isinstance(result, str)


def test_get_systemhw_data_unknown_param():
    result = get_systemhw_data("nonexistent")
    assert result == ""


def test_get_systemhw_data_empty_param():
    result = get_systemhw_data("")
    assert result == ""

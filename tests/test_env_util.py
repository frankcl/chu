"""Tests for shared environment-variable parsing helpers."""

from utils.env_util import env_bool, env_float, env_int, env_list, env_str


def test_env_str_reads_value_and_defaults(monkeypatch):
    monkeypatch.delenv("ENV_STR_TEST", raising=False)
    assert env_str("ENV_STR_TEST", "fallback") == "fallback"
    monkeypatch.setenv("ENV_STR_TEST", "")
    assert env_str("ENV_STR_TEST", "fallback") == "fallback"
    monkeypatch.setenv("ENV_STR_TEST", "value")
    assert env_str("ENV_STR_TEST", "fallback") == "value"


def test_env_int_reads_value_and_falls_back(monkeypatch):
    monkeypatch.delenv("ENV_INT_TEST", raising=False)
    assert env_int("ENV_INT_TEST", 7) == 7
    monkeypatch.setenv("ENV_INT_TEST", "")
    assert env_int("ENV_INT_TEST", 7) == 7
    monkeypatch.setenv("ENV_INT_TEST", "42")
    assert env_int("ENV_INT_TEST", 7) == 42
    monkeypatch.setenv("ENV_INT_TEST", "bad")
    assert env_int("ENV_INT_TEST", 7) == 7


def test_env_float_reads_value_and_falls_back(monkeypatch):
    monkeypatch.delenv("ENV_FLOAT_TEST", raising=False)
    assert env_float("ENV_FLOAT_TEST", 1.5) == 1.5
    monkeypatch.setenv("ENV_FLOAT_TEST", "2.25")
    assert env_float("ENV_FLOAT_TEST", 1.5) == 2.25
    monkeypatch.setenv("ENV_FLOAT_TEST", "bad")
    assert env_float("ENV_FLOAT_TEST", 1.5) == 1.5


def test_env_bool_reads_common_forms_and_falls_back(monkeypatch):
    for raw in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("ENV_BOOL_TEST", raw)
        assert env_bool("ENV_BOOL_TEST", False) is True
    for raw in ("0", "false", "no", "off", "FALSE"):
        monkeypatch.setenv("ENV_BOOL_TEST", raw)
        assert env_bool("ENV_BOOL_TEST", True) is False
    monkeypatch.delenv("ENV_BOOL_TEST", raising=False)
    assert env_bool("ENV_BOOL_TEST", True) is True
    monkeypatch.setenv("ENV_BOOL_TEST", "sometimes")
    assert env_bool("ENV_BOOL_TEST", False) is False


def test_env_list_splits_trims_and_defaults(monkeypatch):
    monkeypatch.delenv("ENV_LIST_TEST", raising=False)
    assert env_list("ENV_LIST_TEST") == []
    assert env_list("ENV_LIST_TEST", ["a"]) == ["a"]
    monkeypatch.setenv("ENV_LIST_TEST", "")
    assert env_list("ENV_LIST_TEST", ["a"]) == ["a"]
    monkeypatch.setenv("ENV_LIST_TEST", "a, b,,c ")
    assert env_list("ENV_LIST_TEST") == ["a", "b", "c"]
    monkeypatch.setenv("ENV_LIST_TEST", "a| b || c ")
    assert env_list("ENV_LIST_TEST", sep="|") == ["a", "b", "c"]

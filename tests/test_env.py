"""Environment reading.

GitHub renders an unset repository variable as an empty string rather than
omitting it, so `os.environ.get(name, default)` returns "" and the default never
applies. That crashed the first live cost-report run with
`ValueError: could not convert string to float: ''`, and it would have crashed
every numeric setting in the system the same way.
"""

import pytest

from lozatron.core import env_flag, env_float, env_int, env_list, env_text


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("X_NUM", "X_TXT", "X_FLAG", "X_A", "X_B"):
        monkeypatch.delenv(name, raising=False)


def test_empty_variable_falls_back_to_the_default(monkeypatch):
    """The exact shape of the live failure: set, but empty."""
    monkeypatch.setenv("X_NUM", "")
    assert env_float("X_NUM", 8.0) == 8.0
    assert env_int("X_NUM", 6) == 6
    assert env_text("X_NUM", "fallback") == "fallback"


def test_absent_variable_falls_back_too():
    assert env_float("X_NUM", 8.0) == 8.0
    assert env_int("X_NUM", 6) == 6


def test_whitespace_only_counts_as_empty(monkeypatch):
    monkeypatch.setenv("X_NUM", "   ")
    assert env_float("X_NUM", 8.0) == 8.0


def test_a_real_value_is_used(monkeypatch):
    monkeypatch.setenv("X_NUM", "2.50")
    assert env_float("X_NUM", 8.0) == 2.5


def test_unparseable_value_falls_back_rather_than_raising(monkeypatch):
    """A typo in a cap must not take delivery down."""
    monkeypatch.setenv("X_NUM", "one dollar")
    assert env_float("X_NUM", 8.0) == 8.0
    assert env_int("X_NUM", 6) == 6


def test_flag_requires_the_literal_true(monkeypatch):
    for value, expected in [("true", True), ("TRUE", True), ("", False),
                            ("false", False), ("1", False), ("yes", False)]:
        monkeypatch.setenv("X_FLAG", value)
        assert env_flag("X_FLAG") is expected, value


def test_list_takes_the_first_non_empty_name(monkeypatch):
    monkeypatch.setenv("X_A", "")
    monkeypatch.setenv("X_B", "second@example.com")
    assert env_list("X_A", "X_B") == ["second@example.com"]


def test_list_splits_and_trims(monkeypatch):
    monkeypatch.setenv("X_A", " one@example.com , two@example.com ,")
    assert env_list("X_A", "X_B") == ["one@example.com", "two@example.com"]


def test_list_is_empty_when_every_name_is_empty(monkeypatch):
    monkeypatch.setenv("X_A", "")
    assert env_list("X_A", "X_B") == []


def test_ops_recipients_fall_back_when_the_secret_is_empty(monkeypatch):
    """An ops secret that is set but blank must not silence the report."""
    from lozatron import gmail
    monkeypatch.setenv("LOZ_OPS_EMAILS", "")
    monkeypatch.setenv("LOZ_CC_EMAILS", "krish@example.com")
    assert gmail.ops_recipients() == ["krish@example.com"]

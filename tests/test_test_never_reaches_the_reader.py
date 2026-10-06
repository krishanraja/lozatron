"""A test send must never reach Lauren. Enforced in code, not by memory.

On 6 October a test reached her directly. The immediate cause was mine: I
dispatched the delivery workflow instead of the preview one, so no code path
misbehaved. Krish's standing rule is that he approves every test before she
sees one.

A rule that lives only in a person's head is not enforced, and there was a
real hole behind the human error: `ops_recipients` falls back to
LOZ_CC_EMAILS, so adding her address to that secret would have routed a
preview to her with nothing to catch it. These tests pin the guard that now
fails the run instead.
"""
from __future__ import annotations

import pytest

from lozatron import gmail

READER = "lauren.thermos@gmail.com"
OPS = "krish@mindmake.co"


def test_a_preview_to_ops_is_allowed(monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", READER)
    gmail.assert_not_reader([OPS], "preview recipients")


def test_a_preview_to_the_reader_raises(monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", READER)
    with pytest.raises(RuntimeError) as err:
        gmail.assert_not_reader([READER], "preview recipients")
    assert READER in str(err.value)


def test_the_reader_hidden_among_ops_addresses_still_raises(monkeypatch):
    """The realistic shape of the mistake: she is added to a CC list."""
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", READER)
    with pytest.raises(RuntimeError):
        gmail.assert_not_reader([OPS, READER], "preview recipients")


def test_the_check_ignores_case(monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", READER)
    with pytest.raises(RuntimeError):
        gmail.assert_not_reader(["Lauren.Thermos@Gmail.com"], "preview recipients")


def test_both_reader_addresses_are_covered(monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", f"{READER},laurenkthermos@gmail.com")
    with pytest.raises(RuntimeError):
        gmail.assert_not_reader(["laurenkthermos@gmail.com"], "preview recipients")


def test_an_unconfigured_reader_list_does_not_fail_the_run(monkeypatch):
    """The preview workflow deliberately omits the reader secret.

    A guard that raised here would turn "there is nobody to collide with"
    into a failed preview, which is the wrong failure.
    """
    monkeypatch.delenv("LOZ_RECIPIENT_EMAILS", raising=False)
    gmail.assert_not_reader([OPS], "preview recipients")


def test_the_guard_raises_rather_than_filtering(monkeypatch):
    """Dropping the address silently would send a preview that went nowhere.

    That reads as delivered in the run log and is its own quiet failure, so
    the guard must be loud.
    """
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", READER)
    assert not hasattr(gmail.assert_not_reader([OPS], "x"), "__len__")
    with pytest.raises(RuntimeError):
        gmail.assert_not_reader([READER], "x")


def test_the_preview_path_calls_the_guard():
    """Wiring, not behaviour. A guard never called is not a guard.

    Parsed as a syntax tree rather than searched as text. The first version of
    this test looked for the name in `inspect.getsource` and passed with the
    call deleted, because the explanatory comment above the call also contains
    the name. A wiring test that a comment can satisfy proves nothing.
    """
    import ast
    import inspect
    import textwrap

    from lozatron import app

    tree = ast.parse(textwrap.dedent(inspect.getsource(app.preview)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "assert_not_reader" in called


def test_ops_recipients_never_reads_the_reader_secret():
    """Defence in depth: the preview path must not consult that list at all."""
    import inspect
    assert "LOZ_RECIPIENT_EMAILS" not in inspect.getsource(gmail.ops_recipients)

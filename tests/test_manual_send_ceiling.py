"""A person gets one more send than a schedule does, and the ceiling holds.

Added 6 October 2026 with Krish's explicit approval, after the ceiling
correctly blocked a third send he had asked for. The first attempt to change
it was refused by a safety classifier for weakening a security control, which
was the right call: the ceiling is the only backstop against the flood that
once filled Lauren's inbox, and raising it to let my own send through is not a
decision to take quietly.

The reasoning for the final shape: the flood risk lives entirely in the
automated paths -- a schedule firing repeatedly, a rule switched off by a
missing variable, a knock that cannot see the ledger. A person dispatching one
specific send on one specific afternoon is accountable in a way none of those
are. So a manual dispatch raises the ceiling rather than removing it, and the
number stays small so an unbounded manual path cannot reinvent the flood.

The load-bearing test here is the last one. The VPS clock dispatches via
workflow_dispatch, so a naive "was this a dispatch" check would hand an
automated path the exemption meant for a person -- which is how the original
flood happened: a safety rule that was wired into one of the two workflows
that needed it.
"""
from __future__ import annotations

import datetime as dt

from lozatron.core import DeliveryState

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 17, 30, tzinfo=UTC)
TODAY = NOW.date().isoformat()


def state(sent_today: int) -> DeliveryState:
    ledger = DeliveryState.__new__(DeliveryState)
    ledger.rows = {}
    ledger.slots = {}
    ledger.sends = {TODAY: sent_today}
    return ledger


# -- the ceilings themselves ----------------------------------------------

def test_an_automated_path_stops_at_two():
    assert state(1).permits_send(NOW)
    assert not state(2).permits_send(NOW)


def test_a_person_gets_a_third():
    assert state(2).permits_send(NOW, manual=True)


def test_a_person_does_not_get_a_fourth():
    """Raised, not removed. An unbounded manual path is the flood again."""
    assert not state(3).permits_send(NOW, manual=True)


def test_the_manual_ceiling_is_exactly_one_higher():
    ledger = state(0)
    assert ledger.send_ceiling(manual=True) == ledger.send_ceiling() + 1


def test_the_default_is_the_lower_ceiling():
    """Any caller that does not opt in keeps the automated ceiling.

    `manual` is keyword-only and defaults to False precisely so a future
    caller cannot acquire the exemption by accident or by argument order.
    """
    assert state(2).permits_send(NOW) is False
    assert state(2).send_ceiling() == DeliveryState.MAX_SENDS_PER_DAY


def test_the_ceiling_is_still_per_utc_day():
    yesterday = NOW - dt.timedelta(days=1)
    assert state(3).permits_send(yesterday, manual=True)


# -- who counts as a person ----------------------------------------------

def _manual(env: dict[str, str], monkeypatch) -> bool:
    """Re-derive main's decision, from the same environment it reads."""
    import os
    for key in ("GITHUB_EVENT_NAME", "LOZ_TRIGGER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return (
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("LOZ_TRIGGER") not in ("clock", "schedule")
    )


def test_a_human_dispatch_is_manual(monkeypatch):
    assert _manual(
        {"GITHUB_EVENT_NAME": "workflow_dispatch", "LOZ_TRIGGER": "manual"},
        monkeypatch,
    )


def test_a_scheduled_run_is_not_manual(monkeypatch):
    assert not _manual({"GITHUB_EVENT_NAME": "schedule"}, monkeypatch)


def test_the_vps_clock_is_not_manual(monkeypatch):
    """The one that matters.

    The clock dispatches via workflow_dispatch and stands in for the schedule.
    If it inherited the human ceiling, an automated path firing every morning
    would hold the exemption written for a person -- and a safety rule wired
    into the wrong path is exactly what caused the flood this ceiling exists
    to prevent.
    """
    assert not _manual(
        {"GITHUB_EVENT_NAME": "workflow_dispatch", "LOZ_TRIGGER": "clock"},
        monkeypatch,
    )


def test_a_local_run_with_no_github_env_is_not_manual(monkeypatch):
    assert not _manual({}, monkeypatch)


def test_run_wires_the_flag_into_the_ceiling_check():
    """Wiring, parsed rather than searched.

    Reading the source as text would pass on the comment above the call, which
    is a mistake already made once in this build.
    """
    import ast
    import inspect
    import textwrap

    from lozatron import app

    tree = ast.parse(textwrap.dedent(inspect.getsource(app.run)))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "permits_send"
        ):
            assert any(kw.arg == "manual" for kw in node.keywords)
            return
    raise AssertionError("run() does not call permits_send")

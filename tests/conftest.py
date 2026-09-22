"""Test isolation.

`app.run` fetches the mechanics pool directly, so without this every test that
exercises the orchestrator makes real network calls: the suite went from 0.2s
to 17.9s and live essays leaked into a fingerprint assertion. Tests that want
the mechanics path stub it explicitly.
"""
import pytest


@pytest.fixture(autouse=True)
def no_network_mechanics(monkeypatch):
    from lozatron import app
    monkeypatch.setattr(app, "analysis_stories", lambda now=None: ([], []), raising=False)

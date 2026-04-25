"""Shared fixtures for the API test suite. Requires DATABASE_URL + FII_USE_FAKE_MODEL=1."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _force_fake_model(monkeypatch):
    monkeypatch.setenv("FII_USE_FAKE_MODEL", "1")
    # Tests don't exercise the LISTEN/NOTIFY listener; disabling it avoids a teardown
    # deadlock where psycopg's notifies() iteration doesn't cancel cleanly when
    # TestClient lifespan rebuilds across tests in the same process.
    monkeypatch.setenv("FII_DISABLE_LISTENER", "1")


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("DATABASE_URL"):
        skip = pytest.mark.skip(reason="DATABASE_URL not set")
        for item in items:
            item.add_marker(skip)

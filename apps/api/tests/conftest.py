"""Shared fixtures for the API test suite. Requires DATABASE_URL + FII_USE_FAKE_MODEL=1."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _force_fake_model(monkeypatch):
    monkeypatch.setenv("FII_USE_FAKE_MODEL", "1")


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("DATABASE_URL"):
        skip = pytest.mark.skip(reason="DATABASE_URL not set")
        for item in items:
            item.add_marker(skip)

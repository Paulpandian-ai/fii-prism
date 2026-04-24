"""Shared fixtures for the agents test suite.

These tests require a real Postgres. Use the DATABASE_URL env var to point at a
local pgvector-enabled instance. If it's unset we skip everything.
"""

from __future__ import annotations

import os

import pytest
from fii_db import get_engine, get_session_factory


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture(scope="session")
def session_factory(database_url: str):
    engine = get_engine(database_url)
    return get_session_factory(engine)


@pytest.fixture(autouse=True)
def _force_fake_model(monkeypatch):
    monkeypatch.setenv("FII_USE_FAKE_MODEL", "1")

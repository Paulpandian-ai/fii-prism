"""Shared fixtures for the ingest test suite. Requires DATABASE_URL."""

from __future__ import annotations

import os

import pytest
from fii_db import get_engine, get_session_factory


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("DATABASE_URL"):
        skip = pytest.mark.skip(reason="DATABASE_URL not set")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture(scope="session")
def session_factory(database_url: str):
    engine = get_engine(database_url)
    return get_session_factory(engine)

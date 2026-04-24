"""Shared fixtures for the schema test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "valid_outputs.json"


@pytest.fixture(scope="session")
def valid_outputs() -> dict:
    with FIXTURE_PATH.open() as f:
        return json.load(f)

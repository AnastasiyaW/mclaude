"""Shared pytest fixtures."""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from mclaude.hub.server import HubConfig, create_app
from mclaude.hub.store import Store


@pytest.fixture
def store() -> Iterator[Store]:
    """In-memory store for isolated unit tests."""
    s = Store(db_path=":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app_client() -> Iterator[TestClient]:
    """FastAPI TestClient with an anonymous in-memory config.

    Anonymous mode means no bearer token is required. The synthetic token
    info has project_id='default' so any project-scoped data lives under
    that single project in the in-memory SQLite.
    """
    config = HubConfig(db_path=":memory:", allow_anonymous=True)
    app = create_app(config)
    with TestClient(app) as client:
        yield client

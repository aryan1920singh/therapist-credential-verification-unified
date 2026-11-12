"""Live PostgreSQL connectivity tests.

These tests require a real PostgreSQL server and are skipped unless the
``TEST_DATABASE_URL`` environment variable is set. They never create tables.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="TEST_DATABASE_URL is not set; live PostgreSQL test skipped",
    ),
]


def test_live_postgresql_connection_executes_select_one() -> None:
    assert TEST_DATABASE_URL is not None
    url = make_url(TEST_DATABASE_URL)
    assert url.drivername.startswith("postgresql")

    database_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with database_engine.connect() as connection:
            assert connection.dialect.name == "postgresql"
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        database_engine.dispose()

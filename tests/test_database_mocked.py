"""Database foundation tests that use mocks and never open a connection."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import app.db.session as session_module
from app.db.base import Base
from app.db.session import check_database_connection, get_db


def test_base_is_a_declarative_base() -> None:
    assert Base.metadata is not None
    assert issubclass(Base, object)
    assert hasattr(Base, "registry")


def test_get_db_yields_a_session_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_module, "SessionLocal", lambda: fake_session)

    dependency = get_db()
    assert next(dependency) is fake_session

    with pytest.raises(StopIteration):
        next(dependency)

    fake_session.close.assert_called_once_with()


def test_get_db_closes_the_session_when_an_exception_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_module, "SessionLocal", lambda: fake_session)

    dependency = get_db()
    next(dependency)

    with pytest.raises(RuntimeError, match="boom"):
        dependency.throw(RuntimeError("boom"))

    fake_session.close.assert_called_once_with()


def test_check_database_connection_returns_true_for_a_successful_query() -> None:
    fake_connection = MagicMock()
    fake_context = MagicMock()
    fake_context.__enter__.return_value = fake_connection
    fake_context.__exit__.return_value = False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = fake_context

    assert check_database_connection(fake_engine) is True
    fake_connection.execute.assert_called_once()


def test_check_database_connection_returns_false_when_connecting_fails() -> None:
    fake_engine = MagicMock()
    fake_engine.connect.side_effect = SQLAlchemyError("connection refused")

    assert check_database_connection(fake_engine) is False


def test_check_database_connection_accepts_no_explicit_engine() -> None:
    original_engine = session_module.engine
    fake_engine = MagicMock()
    fake_connection = MagicMock()
    fake_context = MagicMock()
    fake_context.__enter__.return_value = fake_connection
    fake_context.__exit__.return_value = False
    fake_engine.connect.return_value = fake_context

    session_module.engine = fake_engine
    try:
        assert check_database_connection() is True
    finally:
        session_module.engine = original_engine

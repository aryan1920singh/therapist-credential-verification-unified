from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models import (
    Credential,
    CredentialDocument,
    DocumentChunk,
    DocumentProcessingRecord,
    TherapistProfile,
    User,
)


def _check_constraints(table) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _unique_constraints(table) -> dict[str, set[str]]:
    return {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes}


def _processing_record(
    document_id: uuid.UUID, **overrides
) -> DocumentProcessingRecord:
    values = {
        "document_id": document_id,
        "model_name": "baseline-classifier",
        "model_version": "1.0.0",
        "predicted_category": "license",
        "confidence_score": Decimal("0.9500"),
        "processing_status": "completed",
    }
    values.update(overrides)
    return DocumentProcessingRecord(**values)


def _chunk(document_id: uuid.UUID, chunk_index: int, **overrides) -> DocumentChunk:
    values = {
        "document_id": document_id,
        "chunk_index": chunk_index,
        "chunk_text": f"Temporary chunk {chunk_index}",
    }
    values.update(overrides)
    return DocumentChunk(**values)


def _create_document(session: Session) -> CredentialDocument:
    marker = uuid.uuid4()
    user = User(
        full_name="Module 5B Temporary User",
        email=f"module5b-{marker}@example.invalid",
        role="applicant",
    )
    session.add(user)
    session.flush()

    profile = TherapistProfile(
        user_id=user.id,
        professional_name="Module 5B Temporary Profile",
    )
    session.add(profile)
    session.flush()

    credential = Credential(
        therapist_id=profile.id,
        credential_type="license",
        issuing_organization="Module 5B Temporary Organization",
        status="pending",
    )
    session.add(credential)
    session.flush()

    document = CredentialDocument(
        credential_id=credential.id,
        uploaded_by_user_id=user.id,
        original_filename="temporary.pdf",
        storage_key=f"module5b/{marker}.pdf",
        content_type="application/pdf",
        file_size_bytes=1024,
        sha256_checksum="0" * 64,
    )
    session.add(document)
    session.flush()
    return document


def _expect_integrity_error(session: Session, instance) -> None:
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(instance)
            session.flush()


@pytest.fixture(scope="module")
def database_engine():
    database_engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def db_session(database_engine) -> Session:
    session = Session(database_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_document_models_import_and_register() -> None:
    assert DocumentProcessingRecord.__tablename__ == "document_processing_records"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert {
        "document_processing_records",
        "document_chunks",
    } <= set(Base.metadata.tables)


def test_processing_record_metadata_constraints() -> None:
    checks = _check_constraints(DocumentProcessingRecord.__table__)
    indexes = _index_names(DocumentProcessingRecord.__table__)

    assert "ck_document_processing_records_predicted_category" in checks
    for category in ("degree", "license", "training", "supervision", "other"):
        assert category in checks[
            "ck_document_processing_records_predicted_category"
        ]

    assert "ck_document_processing_records_processing_status" in checks
    for status in ("pending", "processing", "completed", "failed"):
        assert status in checks[
            "ck_document_processing_records_processing_status"
        ]

    assert "ck_document_processing_records_confidence_score_range" in checks
    assert "ix_document_processing_records_document_id" in indexes
    assert "ix_document_processing_records_processing_status" in indexes


def test_chunk_metadata_constraints_and_vector() -> None:
    table = DocumentChunk.__table__
    checks = _check_constraints(table)
    uniques = _unique_constraints(table)
    indexes = _index_names(table)
    metadata_column = table.c["metadata"]
    embedding_column = table.c.embedding

    assert "ck_document_chunks_chunk_index_non_negative" in checks
    assert "ck_document_chunks_page_number_positive" in checks
    assert "ck_document_chunks_embedding_requires_model" in checks
    assert uniques["uq_document_chunks_document_id_chunk_index"] == {
        "document_id",
        "chunk_index",
    }
    assert "ix_document_chunks_document_id" in indexes
    assert isinstance(metadata_column.type, JSONB)
    assert metadata_column.nullable is False
    default_factory = metadata_column.default.arg
    assert callable(default_factory)
    assert default_factory(None) == {}
    assert default_factory(None) is not default_factory(None)
    assert isinstance(embedding_column.type, Vector)
    assert embedding_column.type.dim is None
    assert embedding_column.nullable is True


@pytest.mark.integration
def test_processing_record_can_be_created(db_session: Session) -> None:
    document = _create_document(db_session)
    record = _processing_record(document.id)
    db_session.add(record)
    db_session.flush()

    stored = db_session.get(DocumentProcessingRecord, record.id)
    assert stored is not None
    assert stored.document_id == document.id
    assert stored.predicted_category == "license"
    assert stored.processing_status == "completed"
    assert stored.confidence_score == Decimal("0.9500")


@pytest.mark.integration
def test_invalid_processing_category_and_status_are_rejected(
    db_session: Session,
) -> None:
    document = _create_document(db_session)

    _expect_integrity_error(
        db_session,
        _processing_record(document.id, predicted_category="not-a-category"),
    )
    _expect_integrity_error(
        db_session,
        _processing_record(document.id, processing_status="not-a-status"),
    )


@pytest.mark.integration
def test_confidence_score_outside_zero_to_one_is_rejected(
    db_session: Session,
) -> None:
    document = _create_document(db_session)

    _expect_integrity_error(
        db_session,
        _processing_record(document.id, confidence_score=Decimal("-0.0001")),
    )
    _expect_integrity_error(
        db_session,
        _processing_record(document.id, confidence_score=Decimal("1.0001")),
    )


@pytest.mark.integration
def test_confidence_score_boundaries_are_accepted(db_session: Session) -> None:
    document = _create_document(db_session)
    lower = _processing_record(document.id, confidence_score=Decimal("0"))
    upper = _processing_record(document.id, confidence_score=Decimal("1"))

    db_session.add_all([lower, upper])
    db_session.flush()

    assert lower.id is not None
    assert upper.id is not None


@pytest.mark.integration
def test_multiple_processing_records_can_reference_one_document(
    db_session: Session,
) -> None:
    document = _create_document(db_session)
    db_session.add_all(
        [
            _processing_record(document.id, processing_status="pending"),
            _processing_record(document.id, processing_status="completed"),
        ]
    )
    db_session.flush()

    count = db_session.scalar(
        select(func.count())
        .select_from(DocumentProcessingRecord)
        .where(DocumentProcessingRecord.document_id == document.id)
    )
    assert count == 2


@pytest.mark.integration
def test_document_can_have_multiple_chunks(db_session: Session) -> None:
    document = _create_document(db_session)
    db_session.add_all([_chunk(document.id, 0), _chunk(document.id, 1)])
    db_session.flush()

    count = db_session.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
    )
    assert count == 2


@pytest.mark.integration
def test_duplicate_document_chunk_index_is_rejected(db_session: Session) -> None:
    document = _create_document(db_session)
    db_session.add(_chunk(document.id, 0))
    db_session.flush()

    _expect_integrity_error(db_session, _chunk(document.id, 0))


@pytest.mark.integration
def test_negative_chunk_index_is_rejected(db_session: Session) -> None:
    document = _create_document(db_session)

    _expect_integrity_error(db_session, _chunk(document.id, -1))


@pytest.mark.integration
def test_zero_or_negative_page_number_is_rejected(db_session: Session) -> None:
    document = _create_document(db_session)

    _expect_integrity_error(
        db_session, _chunk(document.id, 0, page_number=0)
    )
    _expect_integrity_error(
        db_session, _chunk(document.id, 0, page_number=-1)
    )


@pytest.mark.integration
def test_chunk_can_be_stored_without_embedding(db_session: Session) -> None:
    document = _create_document(db_session)
    chunk = _chunk(document.id, 0)
    db_session.add(chunk)
    db_session.flush()

    stored = db_session.get(DocumentChunk, chunk.id)
    assert stored is not None
    assert stored.embedding is None
    assert stored.embedding_model is None
    assert stored.chunk_metadata == {}


@pytest.mark.integration
def test_chunk_accepts_valid_variable_dimension_embeddings(
    db_session: Session,
) -> None:
    document = _create_document(db_session)
    first = _chunk(
        document.id,
        0,
        embedding=[0.1, 0.2],
        embedding_model="future-embedding-model",
    )
    second = _chunk(
        document.id,
        1,
        embedding=[0.1, 0.2, 0.3],
        embedding_model="future-embedding-model",
    )
    db_session.add_all([first, second])
    db_session.flush()
    db_session.expire_all()

    stored_first = db_session.get(DocumentChunk, first.id)
    stored_second = db_session.get(DocumentChunk, second.id)

    assert stored_first is not None
    assert stored_second is not None
    assert stored_first.embedding is not None
    assert stored_second.embedding is not None
    assert len(stored_first.embedding) == 2
    assert len(stored_second.embedding) == 3
    assert stored_first.chunk_metadata is not stored_second.chunk_metadata


@pytest.mark.integration
def test_embedding_without_embedding_model_is_rejected(
    db_session: Session,
) -> None:
    document = _create_document(db_session)

    _expect_integrity_error(
        db_session,
        _chunk(document.id, 0, embedding=[0.1, 0.2, 0.3]),
    )

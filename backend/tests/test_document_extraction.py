from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    Credential,
    CredentialDocument,
    DocumentChunk,
    DocumentProcessingRecord,
    TherapistProfile,
    User,
)
from app.services.document_extraction import (
    DocumentExtractionService,
    ExtractionRunResult,
)
from app.services.document_storage import PrivateDocumentStorage
from app.services.extracted_text_storage import (
    InvalidExtractedTextKeyError,
    PrivateExtractedTextStorage,
)
from app.services.pdf_extraction import (
    CorruptedPDFError,
    DocumentAlreadyExtractedError,
    ExtractionStorageError,
    SourceDocumentMissingError,
)
from tests.synthetic_pdf import (
    build_extractor,
    embedded_pdf_bytes,
    mixed_pdf_bytes,
    requires_tessdata,
)

CORRUPTED_PDF_BYTES = b"%PDF-1.4\nthis is not a valid pdf body"


@pytest.fixture(scope="module")
def database_engine():
    from sqlalchemy import create_engine

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(database_engine) -> Connection:
    connection = database_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection):
    session = Session(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def document_storage(tmp_path: Path) -> PrivateDocumentStorage:
    return PrivateDocumentStorage(
        tmp_path / "private-documents",
        max_upload_size_bytes=10 * 1024 * 1024,
    )


@pytest.fixture
def extracted_storage(tmp_path: Path) -> PrivateExtractedTextStorage:
    return PrivateExtractedTextStorage(tmp_path / "private-extracted-text")


@pytest.fixture
def credential(db_session: Session) -> Credential:
    marker = uuid.uuid4()
    user = User(
        full_name="Module 7 Temporary User",
        email=f"module7-{marker}@example.invalid",
        role="applicant",
    )
    db_session.add(user)
    db_session.flush()

    profile = TherapistProfile(
        user_id=user.id,
        professional_name="Module 7 Temporary Profile",
    )
    db_session.add(profile)
    db_session.flush()

    credential_row = Credential(
        therapist_id=profile.id,
        credential_type="license",
        issuing_organization="Module 7 Temporary Organization",
        status="pending",
    )
    db_session.add(credential_row)
    db_session.flush()
    return credential_row


def _service(
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
    extractor=None,
) -> DocumentExtractionService:
    return DocumentExtractionService(
        document_storage,
        extracted_storage,
        extractor or build_extractor(),
    )


def _store_document(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    payload: bytes,
) -> CredentialDocument:
    stored = document_storage.save_pdf(
        BytesIO(payload),
        declared_content_type="application/pdf",
        original_filename="synthetic.pdf",
    )
    profile = db_session.get(TherapistProfile, credential.therapist_id)
    document = CredentialDocument(
        credential_id=credential.id,
        uploaded_by_user_id=profile.user_id,
        original_filename=stored.original_filename,
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        file_size_bytes=stored.file_size_bytes,
        sha256_checksum=stored.sha256_checksum,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _add_document_row(
    db_session: Session,
    credential: Credential,
) -> CredentialDocument:
    profile = db_session.get(TherapistProfile, credential.therapist_id)
    document = CredentialDocument(
        credential_id=credential.id,
        uploaded_by_user_id=profile.user_id,
        original_filename="missing-source.pdf",
        storage_key=secrets.token_hex(32) + ".pdf",
        content_type="application/pdf",
        file_size_bytes=128,
        sha256_checksum="a" * 64,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _records(
    db_session: Session, document_id: uuid.UUID
) -> list[DocumentProcessingRecord]:
    return list(
        db_session.scalars(
            select(DocumentProcessingRecord)
            .where(DocumentProcessingRecord.document_id == document_id)
            .order_by(
                DocumentProcessingRecord.created_at,
                DocumentProcessingRecord.id,
            )
        ).all()
    )


def _chunks(
    db_session: Session, document_id: uuid.UUID
) -> list[DocumentChunk]:
    return list(
        db_session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    )


def _artifacts(extracted_storage: PrivateExtractedTextStorage) -> list[Path]:
    if not extracted_storage.root_dir.exists():
        return []
    return sorted(extracted_storage.root_dir.glob("*.json"))


def test_missing_document_reference_is_rejected(
    db_session: Session,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    orphan = CredentialDocument(
        id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        uploaded_by_user_id=uuid.uuid4(),
        original_filename="ghost.pdf",
        storage_key=secrets.token_hex(32) + ".pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        sha256_checksum="0" * 64,
    )
    service = _service(document_storage, extracted_storage)

    with pytest.raises(SourceDocumentMissingError):
        service.extract(db_session, orphan)

    record_count = db_session.scalar(
        select(func.count())
        .select_from(DocumentProcessingRecord)
        .where(DocumentProcessingRecord.document_id == orphan.id)
    )
    assert record_count == 0


def test_missing_source_file_is_rejected(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    document = _add_document_row(db_session, credential)
    service = _service(document_storage, extracted_storage)

    with pytest.raises(SourceDocumentMissingError):
        service.extract(db_session, document)

    assert _records(db_session, document.id) == []
    assert _chunks(db_session, document.id) == []


@pytest.mark.integration
def test_successful_extraction_persists_record_chunks_and_artifact(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["SERVICE PAGE ONE", "SERVICE PAGE TWO"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )
    source_path = document_storage.path_for(document.storage_key)
    source_digest = hashlib.sha256(payload).hexdigest()
    metadata_before = (
        document.original_filename,
        document.storage_key,
        document.content_type,
        document.file_size_bytes,
        document.sha256_checksum,
    )
    service = _service(document_storage, extracted_storage)

    result: ExtractionRunResult = service.extract(db_session, document)

    assert result.page_count == 2
    assert result.ocr_page_count == 0
    assert result.chunk_count == 2

    records = _records(db_session, document.id)
    assert len(records) == 1
    record = records[0]
    assert record.processing_status == "completed"
    assert record.model_name == "pymupdf+tesseract"
    assert record.model_version.startswith("pymupdf-")
    assert record.predicted_category is None
    assert record.confidence_score is None
    assert record.error_message is None
    assert record.processed_at is not None
    assert record.created_at is not None
    assert result.processing_record.id == record.id

    assert record.extracted_text_reference is not None
    assert re.fullmatch(
        r"[0-9a-f]{64}\.json", record.extracted_text_reference
    )
    artifact_path = extracted_storage.path_for(
        record.extracted_text_reference
    )
    assert artifact_path.parent == extracted_storage.root_dir
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == 1
    assert artifact["document_id"] == str(document.id)
    assert artifact["sha256_checksum"] == source_digest
    assert artifact["page_count"] == 2
    assert artifact["ocr_page_count"] == 0
    assert artifact["text_bytes"] > 0
    assert [page["page_number"] for page in artifact["pages"]] == [1, 2]
    assert artifact["pages"][0]["extraction_method"] == "embedded_text"
    assert artifact["pages"][0]["ocr_confidence"] is None

    chunks = _chunks(db_session, document.id)
    assert len(chunks) == result.chunk_count
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.page_number for chunk in chunks] == [1, 2]
    assert all(chunk.embedding is None for chunk in chunks)
    assert all(chunk.embedding_model is None for chunk in chunks)
    assert chunks[0].chunk_metadata["page_number"] == 1
    assert chunks[0].chunk_metadata["extraction_method"] == "embedded_text"
    assert chunks[0].chunk_metadata["ocr_confidence"] is None
    assert chunks[0].chunk_metadata["source_sha256"] == source_digest
    assert chunks[1].chunk_metadata["page_number"] == 2

    assert source_path.read_bytes() == payload
    assert (
        hashlib.sha256(source_path.read_bytes()).hexdigest() == source_digest
    )

    db_session.refresh(document)
    assert (
        document.original_filename,
        document.storage_key,
        document.content_type,
        document.file_size_bytes,
        document.sha256_checksum,
    ) == metadata_before


@pytest.mark.integration
def test_processing_status_is_processing_during_extraction(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["STATUS OBSERVATION PAGE"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )
    extractor = build_extractor()
    observed_statuses: list[str | None] = []
    original_extract_file = extractor.extract_file

    def observing_extract_file(path):
        observed_statuses.append(
            db_session.scalar(
                select(DocumentProcessingRecord.processing_status).where(
                    DocumentProcessingRecord.document_id == document.id
                )
            )
        )
        return original_extract_file(path)

    extractor.extract_file = observing_extract_file
    service = _service(document_storage, extracted_storage, extractor)

    result = service.extract(db_session, document)

    assert observed_statuses == ["processing"]
    assert result.processing_record.processing_status == "completed"
    assert result.processing_record.processed_at is not None


@pytest.mark.integration
def test_repeated_extraction_attempt_is_rejected(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["REPEAT ATTEMPT PAGE"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )
    service = _service(document_storage, extracted_storage)

    first = service.extract(db_session, document)

    with pytest.raises(DocumentAlreadyExtractedError) as excinfo:
        service.extract(db_session, document)

    assert excinfo.value.code == "document_already_extracted"
    assert excinfo.value.status_code == 409
    assert len(_records(db_session, document.id)) == 1
    assert len(_chunks(db_session, document.id)) == first.chunk_count


@pytest.mark.integration
def test_in_progress_extraction_is_rejected(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["IN PROGRESS PAGE"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )
    db_session.add(
        DocumentProcessingRecord(
            document_id=document.id,
            model_name="probe",
            model_version="1",
            processing_status="processing",
        )
    )
    db_session.flush()
    service = _service(document_storage, extracted_storage)

    with pytest.raises(DocumentAlreadyExtractedError) as excinfo:
        service.extract(db_session, document)

    assert "already being extracted" in str(excinfo.value)
    assert len(_chunks(db_session, document.id)) == 0


@pytest.mark.integration
def test_failed_extraction_marks_record_and_allows_retry(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["RETRY AFTER FAILURE PAGE"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )
    source_path = document_storage.path_for(document.storage_key)
    source_path.write_bytes(CORRUPTED_PDF_BYTES)
    service = _service(document_storage, extracted_storage)

    with pytest.raises(CorruptedPDFError):
        service.extract(db_session, document)

    records = _records(db_session, document.id)
    assert len(records) == 1
    assert records[0].processing_status == "failed"
    assert records[0].error_message == "corrupted_pdf"
    assert records[0].extracted_text_reference is None
    assert records[0].processed_at is not None
    assert _chunks(db_session, document.id) == []
    assert _artifacts(extracted_storage) == []

    source_path.write_bytes(payload)
    retry_result = service.extract(db_session, document)

    records = _records(db_session, document.id)
    assert len(records) == 2
    assert sorted(record.processing_status for record in records) == [
        "completed",
        "failed",
    ]
    completed = next(
        record
        for record in records
        if record.processing_status == "completed"
    )
    assert completed.extracted_text_reference is not None
    assert completed.error_message is None
    assert len(_artifacts(extracted_storage)) == 1
    assert len(_chunks(db_session, document.id)) == retry_result.chunk_count


@requires_tessdata
@pytest.mark.integration
def test_mixed_pdf_records_page_aware_chunks_with_ocr_confidence(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    document = _store_document(
        db_session, credential, document_storage, mixed_pdf_bytes()
    )
    service = _service(document_storage, extracted_storage)

    result = service.extract(db_session, document)

    assert result.page_count == 2
    assert result.ocr_page_count == 1

    chunks = _chunks(db_session, document.id)
    assert len(chunks) == result.chunk_count
    page_one_chunks = [chunk for chunk in chunks if chunk.page_number == 1]
    page_two_chunks = [chunk for chunk in chunks if chunk.page_number == 2]
    assert page_one_chunks
    assert page_two_chunks
    for chunk in page_one_chunks:
        assert chunk.chunk_metadata["extraction_method"] == "embedded_text"
        assert chunk.chunk_metadata["ocr_confidence"] is None
    for chunk in page_two_chunks:
        assert chunk.chunk_metadata["extraction_method"] == "ocr"
        confidence = chunk.chunk_metadata["ocr_confidence"]
        assert isinstance(confidence, (int, float))
        assert 0.0 < float(confidence) <= 1.0

    record = _records(db_session, document.id)[0]
    artifact = json.loads(
        extracted_storage.path_for(record.extracted_text_reference).read_text(
            encoding="utf-8"
        )
    )
    assert artifact["ocr_page_count"] == 1
    assert artifact["pages"][0]["extraction_method"] == "embedded_text"
    assert artifact["pages"][0]["ocr_confidence"] is None
    assert artifact["pages"][1]["extraction_method"] == "ocr"
    assert artifact["pages"][1]["ocr_confidence"] == float(
        page_two_chunks[0].chunk_metadata["ocr_confidence"]
    )


@pytest.mark.integration
def test_artifact_removed_when_chunk_persistence_fails(
    db_session: Session,
    credential: Credential,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> None:
    payload = embedded_pdf_bytes(["CLEANUP ON FAILURE PAGE"])
    document = _store_document(
        db_session, credential, document_storage, payload
    )

    class FlakyCommitSession(Session):
        flush_count = 0

        def flush(self, objects=None):
            has_pending = bool(self.new or self.dirty)
            result = super().flush(objects)
            if has_pending:
                type(self).flush_count += 1
                if type(self).flush_count == 2:
                    raise SQLAlchemyError(
                        "simulated chunk persistence failure"
                    )
            return result

    flaky_session = FlakyCommitSession(
        bind=db_session.get_bind(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    service = _service(document_storage, extracted_storage)

    try:
        with pytest.raises(ExtractionStorageError) as excinfo:
            service.extract(flaky_session, document)

        assert excinfo.value.code == "extraction_storage_failed"
        assert excinfo.value.status_code == 500
        assert FlakyCommitSession.flush_count >= 2
    finally:
        flaky_session.rollback()
        flaky_session.close()

    record = _records(db_session, document.id)[0]
    assert record.processing_status == "failed"
    assert record.error_message == "extraction_storage_failed"
    assert record.extracted_text_reference is None
    assert record.processed_at is not None
    assert _chunks(db_session, document.id) == []
    assert _artifacts(extracted_storage) == []
    source_path = document_storage.path_for(document.storage_key)
    assert source_path.read_bytes() == payload


def test_extracted_text_storage_writes_private_random_keys(
    tmp_path: Path,
) -> None:
    storage = PrivateExtractedTextStorage(tmp_path / "artifacts")

    key = storage.save_json({"k": "v"})

    assert re.fullmatch(r"[0-9a-f]{64}\.json", key)
    path = storage.path_for(key)
    assert path.parent == storage.root_dir
    assert path.read_text(encoding="utf-8") == '{"k":"v"}'
    storage.delete(key)
    assert not path.exists()


def test_extracted_text_storage_rejects_unsafe_keys(
    tmp_path: Path,
) -> None:
    storage = PrivateExtractedTextStorage(tmp_path / "artifacts")
    storage.root_dir.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("keep", encoding="utf-8")

    unsafe_keys = [
        "../victim.json",
        str(victim),
        "nested/x.json",
        "nested\\x.json",
        "notes.txt",
        "..",
        ".",
        "",
        "bad\x00.json",
    ]
    for key in unsafe_keys:
        with pytest.raises(InvalidExtractedTextKeyError):
            storage.path_for(key)

    with pytest.raises(InvalidExtractedTextKeyError):
        storage.delete("../victim.json")

    assert victim.read_text(encoding="utf-8") == "keep"

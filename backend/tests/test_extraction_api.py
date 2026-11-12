from __future__ import annotations

import secrets
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import (
    Credential,
    CredentialDocument,
    DocumentChunk,
    DocumentProcessingRecord,
    TherapistProfile,
    User,
)
from app.services.document_storage import (
    PrivateDocumentStorage,
    get_document_storage,
)
from app.services.extracted_text_storage import (
    PrivateExtractedTextStorage,
    get_extracted_text_storage,
)
from app.services.pdf_extraction import (
    TesseractOCRBackend,
    get_pdf_text_extractor,
)
from tests.synthetic_pdf import (
    SYNTHETIC_ENCRYPTION_PASSWORD,
    TESSDATA_PATH,
    build_extractor,
    embedded_pdf_bytes,
    encrypted_pdf_bytes,
    requires_tessdata,
    scanned_pdf_bytes,
)

UPLOAD_URL = "/api/v1/documents"
EXTRACT_URL_TEMPLATE = "/api/v1/documents/{document_id}/extract"
RESPONSE_KEYS = {
    "message",
    "document_id",
    "processing_record_id",
    "processing_status",
    "page_count",
    "ocr_page_count",
    "chunk_count",
    "processed_at",
}


@pytest.fixture(scope="module")
def database_engine():
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
def db_session(db_connection: Connection) -> Session:
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
        full_name="Module 7 API User",
        email=f"module7-api-{marker}@example.invalid",
        role="applicant",
    )
    db_session.add(user)
    db_session.flush()

    profile = TherapistProfile(
        user_id=user.id,
        professional_name="Module 7 API Profile",
    )
    db_session.add(profile)
    db_session.flush()

    credential_row = Credential(
        therapist_id=profile.id,
        credential_type="license",
        issuing_organization="Module 7 API Organization",
        status="pending",
    )
    db_session.add(credential_row)
    db_session.flush()
    return credential_row


@pytest.fixture
def api_client(
    db_connection: Connection,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        session = Session(
            bind=db_connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_document_storage] = (
        lambda: document_storage
    )
    app.dependency_overrides[get_extracted_text_storage] = (
        lambda: extracted_storage
    )
    app.dependency_overrides[get_pdf_text_extractor] = (
        lambda: build_extractor()
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_document_storage, None)
        app.dependency_overrides.pop(get_extracted_text_storage, None)
        app.dependency_overrides.pop(get_pdf_text_extractor, None)


def _set_extractor(extractor) -> None:
    app.dependency_overrides[get_pdf_text_extractor] = lambda: extractor


def _upload(
    client: TestClient,
    credential_id: uuid.UUID,
    content: bytes,
    *,
    filename: str = "credential.pdf",
):
    return client.post(
        UPLOAD_URL,
        data={"credential_id": str(credential_id)},
        files={"file": (filename, content, "application/pdf")},
    )


def _extract(client: TestClient, document_id: uuid.UUID):
    return client.post(EXTRACT_URL_TEMPLATE.format(document_id=document_id))


def _uploaded_document_id(response) -> uuid.UUID:
    return uuid.UUID(response.json()["document"]["id"])


def _records(
    db_session: Session, document_id: uuid.UUID
) -> list[DocumentProcessingRecord]:
    return list(
        db_session.query(DocumentProcessingRecord)
        .filter(DocumentProcessingRecord.document_id == document_id)
        .order_by(
            DocumentProcessingRecord.created_at,
            DocumentProcessingRecord.id,
        )
        .all()
    )


def _chunks(
    db_session: Session, document_id: uuid.UUID
) -> list[DocumentChunk]:
    return list(
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )


def _source_path(
    db_session: Session,
    document_storage: PrivateDocumentStorage,
    document_id: uuid.UUID,
) -> Path:
    document = db_session.get(CredentialDocument, document_id)
    assert document is not None
    return document_storage.path_for(document.storage_key)


@pytest.mark.integration
def test_extract_returns_aggregate_metadata_without_text(
    api_client: TestClient,
    db_session: Session,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
    credential: Credential,
) -> None:
    payload = embedded_pdf_bytes(
        ["API EXTRACTION PAGE ONE", "API EXTRACTION PAGE TWO"]
    )
    upload = _upload(api_client, credential.id, payload)
    assert upload.status_code == 201
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == RESPONSE_KEYS
    assert body["message"] == "Document extraction completed."
    assert body["document_id"] == str(document_id)
    assert body["processing_status"] == "completed"
    assert body["page_count"] == 2
    assert body["ocr_page_count"] == 0
    assert body["chunk_count"] >= 1
    assert body["processed_at"] is not None

    dumped = response.text
    assert "API EXTRACTION" not in dumped
    assert "extracted_text" not in body
    document = db_session.get(CredentialDocument, document_id)
    assert document is not None
    assert document.storage_key not in dumped
    assert str(document.sha256_checksum) not in dumped

    records = _records(db_session, document_id)
    assert len(records) == 1
    record = records[0]
    assert record.processing_status == "completed"
    assert record.id == uuid.UUID(body["processing_record_id"])
    assert record.predicted_category is None
    assert record.confidence_score is None
    assert record.extracted_text_reference is not None
    artifact_path = extracted_storage.path_for(
        record.extracted_text_reference
    )
    assert artifact_path.is_file()

    chunks = _chunks(db_session, document_id)
    assert len(chunks) == body["chunk_count"]
    assert all(chunk.embedding is None for chunk in chunks)


@pytest.mark.integration
def test_extract_missing_document_returns_404(
    api_client: TestClient,
    db_session: Session,
) -> None:
    response = _extract(api_client, uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found."
    count = db_session.query(DocumentProcessingRecord).count()
    assert count == 0


@pytest.mark.integration
def test_extract_missing_source_file_returns_409(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    profile = db_session.get(TherapistProfile, credential.therapist_id)
    assert profile is not None
    document = CredentialDocument(
        credential_id=credential.id,
        uploaded_by_user_id=profile.user_id,
        original_filename="missing-source.pdf",
        storage_key=secrets.token_hex(32) + ".pdf",
        content_type="application/pdf",
        file_size_bytes=128,
        sha256_checksum="b" * 64,
    )
    db_session.add(document)
    db_session.flush()

    response = _extract(api_client, document.id)

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "The stored document file is unavailable."
    )
    assert _records(db_session, document.id) == []


@pytest.mark.integration
def test_extract_twice_returns_409(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    payload = embedded_pdf_bytes(["REPEAT EXTRACTION PAGE"])
    upload = _upload(api_client, credential.id, payload)
    document_id = _uploaded_document_id(upload)

    first = _extract(api_client, document_id)
    assert first.status_code == 201

    second = _extract(api_client, document_id)

    assert second.status_code == 409
    assert (
        second.json()["detail"]
        == "This document has already been extracted."
    )
    assert len(_records(db_session, document_id)) == 1
    assert len(_chunks(db_session, document_id)) == first.json()[
        "chunk_count"
    ]


@pytest.mark.integration
def test_extract_corrupted_pdf_returns_422(
    api_client: TestClient,
    db_session: Session,
    document_storage: PrivateDocumentStorage,
    extracted_storage: PrivateExtractedTextStorage,
    credential: Credential,
) -> None:
    payload = embedded_pdf_bytes(["LATER CORRUPTED PAGE"])
    upload = _upload(api_client, credential.id, payload)
    document_id = _uploaded_document_id(upload)
    source_path = _source_path(
        db_session, document_storage, document_id
    )
    source_path.write_bytes(b"%PDF-1.4\nnot really a pdf")

    response = _extract(api_client, document_id)

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "The PDF could not be opened or parsed."
    )
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].processing_status == "failed"
    assert records[0].error_message == "corrupted_pdf"
    assert records[0].extracted_text_reference is None
    assert _chunks(db_session, document_id) == []
    assert not extracted_storage.root_dir.exists() or list(
        extracted_storage.root_dir.glob("*.json")
    ) == []


@pytest.mark.integration
def test_extract_encrypted_pdf_returns_422(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    upload = _upload(
        api_client, credential.id, encrypted_pdf_bytes()
    )
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Encrypted PDFs cannot be processed."
    )
    assert SYNTHETIC_ENCRYPTION_PASSWORD not in response.text
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].processing_status == "failed"
    assert records[0].error_message == "encrypted_pdf"


@pytest.mark.integration
def test_extract_page_limit_returns_413(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    _set_extractor(build_extractor(max_pages=1))
    payload = embedded_pdf_bytes(["PAGE ONE", "PAGE TWO"])
    upload = _upload(api_client, credential.id, payload)
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 413
    assert (
        response.json()["detail"]
        == "The PDF exceeds the maximum allowed page count."
    )
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].error_message == "page_limit_exceeded"
    assert _chunks(db_session, document_id) == []


@pytest.mark.integration
def test_extract_text_limit_returns_413(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    _set_extractor(build_extractor(max_text_bytes=10))
    payload = embedded_pdf_bytes(["A FAIRLY LONG PAGE OF TEXT"])
    upload = _upload(api_client, credential.id, payload)
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 413
    assert (
        response.json()["detail"]
        == "The PDF exceeds the maximum allowed extracted text size."
    )
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].error_message == "text_limit_exceeded"


@pytest.mark.integration
def test_extract_ocr_unavailable_returns_503(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
    tmp_path: Path,
) -> None:
    _set_extractor(
        build_extractor(
            ocr_backend=TesseractOCRBackend(
                data_dir=tmp_path / "missing-tessdata",
            ),
        )
    )
    upload = _upload(
        api_client, credential.id, scanned_pdf_bytes(["SCANNED PAGE"])
    )
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 503
    assert (
        response.json()["detail"]
        == "OCR is required but the OCR engine is unavailable."
    )
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].processing_status == "failed"
    assert records[0].error_message == "ocr_unavailable"
    assert _chunks(db_session, document_id) == []


@requires_tessdata
@pytest.mark.integration
def test_extract_ocr_timeout_returns_504(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_recognize(self, image):
        import time

        time.sleep(1.0)
        return "", None

    monkeypatch.setattr(
        TesseractOCRBackend, "_recognize_tesserocr", slow_recognize
    )
    _set_extractor(
        build_extractor(
            ocr_backend=TesseractOCRBackend(
                data_dir=TESSDATA_PATH,
                page_timeout_seconds=0.05,
            ),
        )
    )
    upload = _upload(
        api_client, credential.id, scanned_pdf_bytes(["SLOW OCR PAGE"])
    )
    document_id = _uploaded_document_id(upload)

    response = _extract(api_client, document_id)

    assert response.status_code == 504
    assert (
        response.json()["detail"]
        == "OCR exceeded the allowed processing time."
    )
    records = _records(db_session, document_id)
    assert len(records) == 1
    assert records[0].processing_status == "failed"
    assert records[0].error_message == "ocr_timeout"

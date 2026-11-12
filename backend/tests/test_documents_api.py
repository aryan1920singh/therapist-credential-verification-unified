from __future__ import annotations

import hashlib
import uuid
from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import Credential, CredentialDocument, TherapistProfile, User
from app.services.document_storage import (
    InvalidStorageKeyError,
    PrivateDocumentStorage,
    get_document_storage,
)

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"%%EOF\n"
)
UPLOAD_URL = "/api/v1/documents"


def _create_credential(session: Session) -> Credential:
    marker = uuid.uuid4()
    user = User(
        full_name="Module 6 Temporary User",
        email=f"module6-{marker}@example.invalid",
        role="applicant",
    )
    session.add(user)
    session.flush()

    profile = TherapistProfile(
        user_id=user.id,
        professional_name="Module 6 Temporary Profile",
    )
    session.add(profile)
    session.flush()

    credential = Credential(
        therapist_id=profile.id,
        credential_type="license",
        issuing_organization="Module 6 Temporary Organization",
        status="pending",
    )
    session.add(credential)
    session.commit()
    return credential


def _upload(
    client: TestClient,
    credential_id: uuid.UUID,
    *,
    content: bytes = MINIMAL_PDF,
    filename: str = "credential.pdf",
    content_type: str = "application/pdf",
):
    return client.post(
        UPLOAD_URL,
        data={"credential_id": str(credential_id)},
        files={"file": (filename, content, content_type)},
    )


def _stored_files(storage: PrivateDocumentStorage) -> list[Path]:
    if not storage.root_dir.exists():
        return []
    return [
        path for path in storage.root_dir.iterdir() if path.is_file()
    ]


@pytest.fixture(scope="module")
def database_engine():
    database_engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def db_connection(database_engine) -> Generator[Connection, None, None]:
    connection = database_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Generator[Session, None, None]:
    session = Session(bind=db_connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def storage(tmp_path: Path) -> PrivateDocumentStorage:
    return PrivateDocumentStorage(
        tmp_path / "private-documents",
        max_upload_size_bytes=1024 * 1024,
    )


@pytest.fixture
def api_client(
    db_connection: Connection,
    storage: PrivateDocumentStorage,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        session = Session(bind=db_connection, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_document_storage] = lambda: storage
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_document_storage, None)


@pytest.fixture
def credential(db_session: Session) -> Credential:
    return _create_credential(db_session)


def test_storage_generates_unique_keys_and_checksums(
    storage: PrivateDocumentStorage,
) -> None:
    first = storage.save_pdf(
        BytesIO(MINIMAL_PDF),
        declared_content_type="application/pdf",
        original_filename="same-name.pdf",
    )
    second = storage.save_pdf(
        BytesIO(MINIMAL_PDF),
        declared_content_type="application/pdf",
        original_filename="same-name.pdf",
    )

    assert first.storage_key != second.storage_key
    assert first.storage_key.endswith(".pdf")
    assert second.storage_key.endswith(".pdf")
    assert storage.path_for(first.storage_key).read_bytes() == MINIMAL_PDF
    assert storage.path_for(second.storage_key).read_bytes() == MINIMAL_PDF
    assert first.sha256_checksum == hashlib.sha256(MINIMAL_PDF).hexdigest()
    assert first.file_size_bytes == len(MINIMAL_PDF)


def test_storage_rejects_path_traversal_keys(
    storage: PrivateDocumentStorage,
) -> None:
    storage.root_dir.mkdir(parents=True)
    outside_file = storage.root_dir.parent / "outside.pdf"
    outside_file.write_bytes(b"do not delete")

    with pytest.raises(InvalidStorageKeyError):
        storage.delete("../outside.pdf")
    with pytest.raises(InvalidStorageKeyError):
        storage.path_for("nested/file.pdf")
    with pytest.raises(InvalidStorageKeyError):
        storage.path_for(str(outside_file))

    assert outside_file.read_bytes() == b"do not delete"


@pytest.mark.integration
def test_valid_pdf_upload_persists_safe_metadata(
    api_client: TestClient,
    db_session: Session,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    response = _upload(api_client, credential.id)

    assert response.status_code == 201
    body = response.json()
    metadata = body["document"]
    assert body["message"] == "Document uploaded successfully."
    assert metadata["credential_id"] == str(credential.id)
    assert metadata["original_filename"] == "credential.pdf"
    assert metadata["content_type"] == "application/pdf"
    assert metadata["file_size_bytes"] == len(MINIMAL_PDF)
    assert metadata["sha256_checksum"] == hashlib.sha256(
        MINIMAL_PDF
    ).hexdigest()

    document = db_session.get(
        CredentialDocument,
        uuid.UUID(metadata["id"]),
    )
    profile = db_session.get(TherapistProfile, credential.therapist_id)
    assert document is not None
    assert profile is not None
    assert document.uploaded_by_user_id == profile.user_id
    assert document.uploaded_at is not None
    assert document.storage_key != "credential.pdf"
    assert "/" not in document.storage_key
    assert "\\" not in document.storage_key
    assert storage.path_for(document.storage_key).read_bytes() == MINIMAL_PDF


@pytest.mark.integration
def test_empty_upload_is_rejected(
    api_client: TestClient,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    response = _upload(api_client, credential.id, content=b"")

    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is empty."
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_unsupported_content_type_is_rejected(
    api_client: TestClient,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    response = _upload(
        api_client,
        credential.id,
        content_type="text/plain",
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Only PDF documents are supported."
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_incorrect_pdf_signature_is_rejected(
    api_client: TestClient,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    response = _upload(
        api_client,
        credential.id,
        content=b"this is not a PDF",
    )

    assert response.status_code == 400
    assert "valid PDF signature" in response.json()["detail"]
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_maximum_upload_size_is_enforced(
    api_client: TestClient,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    storage.max_upload_size_bytes = 20

    response = _upload(api_client, credential.id)

    assert response.status_code == 413
    assert "maximum allowed size" in response.json()["detail"]
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_invalid_credential_reference_is_rejected(
    api_client: TestClient,
    storage: PrivateDocumentStorage,
) -> None:
    malformed = api_client.post(
        UPLOAD_URL,
        data={"credential_id": "not-a-uuid"},
        files={
            "file": (
                "credential.pdf",
                MINIMAL_PDF,
                "application/pdf",
            )
        },
    )
    missing = _upload(api_client, uuid.uuid4())

    assert malformed.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Credential not found."
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_database_failure_removes_saved_file(
    api_client: TestClient,
    db_connection: Connection,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    class CommitFailingSession(Session):
        def commit(self) -> None:
            raise SQLAlchemyError("simulated commit failure")

    def failing_get_db() -> Generator[Session, None, None]:
        session = CommitFailingSession(
            bind=db_connection,
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    app.dependency_overrides[get_db] = failing_get_db

    response = _upload(api_client, credential.id)

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Document metadata could not be saved."
    )
    assert _stored_files(storage) == []


@pytest.mark.integration
def test_document_metadata_can_be_retrieved(
    api_client: TestClient,
    credential: Credential,
) -> None:
    upload_response = _upload(api_client, credential.id)
    document_id = upload_response.json()["document"]["id"]

    response = api_client.get(f"{UPLOAD_URL}/{document_id}")

    assert response.status_code == 200
    assert response.json() == upload_response.json()["document"]


@pytest.mark.integration
def test_missing_document_metadata_returns_not_found(
    api_client: TestClient,
) -> None:
    response = api_client.get(f"{UPLOAD_URL}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found."


@pytest.mark.integration
def test_credential_document_listing_is_scoped(
    api_client: TestClient,
    db_session: Session,
    credential: Credential,
) -> None:
    second_credential = _create_credential(db_session)
    first_upload = _upload(api_client, credential.id)
    second_upload = _upload(api_client, credential.id)
    other_upload = _upload(api_client, second_credential.id)

    response = api_client.get(
        f"/api/v1/credentials/{credential.id}/documents"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["credential_id"] == str(credential.id)
    assert body["count"] == 2
    assert {document["id"] for document in body["documents"]} == {
        first_upload.json()["document"]["id"],
        second_upload.json()["document"]["id"],
    }
    assert other_upload.json()["document"]["id"] not in {
        document["id"] for document in body["documents"]
    }


@pytest.mark.integration
def test_listing_unknown_credential_returns_not_found(
    api_client: TestClient,
) -> None:
    response = api_client.get(
        f"/api/v1/credentials/{uuid.uuid4()}/documents"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Credential not found."


@pytest.mark.integration
def test_responses_do_not_leak_storage_paths(
    api_client: TestClient,
    db_session: Session,
    storage: PrivateDocumentStorage,
    credential: Credential,
) -> None:
    upload_response = _upload(
        api_client,
        credential.id,
        filename="../../private/path/credential.pdf",
    )
    assert upload_response.status_code == 201
    metadata = upload_response.json()["document"]

    document = db_session.get(
        CredentialDocument,
        uuid.UUID(metadata["id"]),
    )
    assert document is not None

    get_response = api_client.get(f"{UPLOAD_URL}/{metadata['id']}")
    assert get_response.status_code == 200

    for response in (upload_response, get_response):
        body = response.json()
        document_payload = body.get("document", body)
        assert "storage_key" not in document_payload
        assert str(storage.root_dir) not in response.text
        assert document.storage_key not in response.text

    assert metadata["original_filename"] == "credential.pdf"
    assert storage.path_for(document.storage_key).parent == storage.root_dir


@pytest.mark.integration
def test_malformed_upload_request_is_rejected(
    api_client: TestClient,
) -> None:
    response = api_client.post(UPLOAD_URL, data={})

    assert response.status_code == 422

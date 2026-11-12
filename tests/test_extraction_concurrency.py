from __future__ import annotations

import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
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
from app.services.extracted_text_storage import PrivateExtractedTextStorage
from app.services.pdf_extraction import DocumentAlreadyExtractedError
from tests.synthetic_pdf import build_extractor, embedded_pdf_bytes


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def committed_document(engine, tmp_path: Path) -> SimpleNamespace:
    storage = PrivateDocumentStorage(
        tmp_path / "private-documents",
        max_upload_size_bytes=10 * 1024 * 1024,
    )
    extracted_storage = PrivateExtractedTextStorage(
        tmp_path / "private-extracted-text"
    )
    payload = embedded_pdf_bytes(
        ["CONCURRENCY PAGE ONE", "CONCURRENCY PAGE TWO"]
    )
    session = Session(engine, expire_on_commit=False)
    row_ids: dict[str, uuid.UUID] = {}
    try:
        stored = storage.save_pdf(
            BytesIO(payload),
            declared_content_type="application/pdf",
            original_filename="concurrency.pdf",
        )
        marker = uuid.uuid4()
        user = User(
            full_name="Module 7 Concurrency User",
            email=f"module7-concurrency-{marker}@example.invalid",
            role="applicant",
        )
        session.add(user)
        session.flush()

        profile = TherapistProfile(
            user_id=user.id,
            professional_name="Module 7 Concurrency Profile",
        )
        session.add(profile)
        session.flush()

        credential = Credential(
            therapist_id=profile.id,
            credential_type="license",
            issuing_organization="Module 7 Concurrency Organization",
            status="pending",
        )
        session.add(credential)
        session.flush()

        document = CredentialDocument(
            credential_id=credential.id,
            uploaded_by_user_id=profile.user_id,
            original_filename=stored.original_filename,
            storage_key=stored.storage_key,
            content_type=stored.content_type,
            file_size_bytes=stored.file_size_bytes,
            sha256_checksum=stored.sha256_checksum,
        )
        session.add(document)
        session.flush()
        session.commit()

        row_ids = {
            "user": user.id,
            "profile": profile.id,
            "credential": credential.id,
            "document": document.id,
        }
        yield SimpleNamespace(
            document=document,
            storage=storage,
            extracted_storage=extracted_storage,
            service=DocumentExtractionService(
                storage, extracted_storage, build_extractor()
            ),
            row_ids=row_ids,
        )
    finally:
        session.close()
        cleanup = Session(engine)
        try:
            if row_ids:
                cleanup.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.document_id == row_ids["document"]
                    )
                )
                cleanup.execute(
                    delete(DocumentProcessingRecord).where(
                        DocumentProcessingRecord.document_id
                        == row_ids["document"]
                    )
                )
                cleanup.execute(
                    delete(CredentialDocument).where(
                        CredentialDocument.id == row_ids["document"]
                    )
                )
                cleanup.execute(
                    delete(Credential).where(
                        Credential.id == row_ids["credential"]
                    )
                )
                cleanup.execute(
                    delete(TherapistProfile).where(
                        TherapistProfile.id == row_ids["profile"]
                    )
                )
                cleanup.execute(
                    delete(User).where(User.id == row_ids["user"])
                )
            cleanup.commit()
        finally:
            cleanup.close()


@pytest.mark.integration
def test_nowait_probe_fails_while_holder_locks_document(
    engine,
    committed_document: SimpleNamespace,
) -> None:
    document_id = committed_document.document.id
    holder = Session(engine)
    probe = Session(engine)
    try:
        locked_id = holder.execute(
            select(CredentialDocument.id)
            .where(CredentialDocument.id == document_id)
            .with_for_update()
        ).scalar()
        assert locked_id == document_id

        with pytest.raises(OperationalError):
            probe.execute(
                select(CredentialDocument.id)
                .where(CredentialDocument.id == document_id)
                .with_for_update(nowait=True)
            )
    finally:
        probe.close()
        holder.close()


@pytest.mark.integration
def test_concurrent_extract_blocks_until_holder_commits(
    engine,
    committed_document: SimpleNamespace,
) -> None:
    document = committed_document.document
    holder = Session(engine, expire_on_commit=False)
    outcomes: dict[str, object] = {}
    thread: threading.Thread | None = None
    try:
        locked_id = holder.execute(
            select(CredentialDocument.id)
            .where(CredentialDocument.id == document.id)
            .with_for_update()
        ).scalar()
        assert locked_id == document.id
        holder.add(
            DocumentProcessingRecord(
                document_id=document.id,
                model_name="holder",
                model_version="1",
                processing_status="processing",
            )
        )
        holder.flush()

        def run_extract() -> None:
            session = Session(engine, expire_on_commit=False)
            try:
                outcomes["result"] = committed_document.service.extract(
                    session, document
                )
            except BaseException as exc:
                outcomes["error"] = exc
            finally:
                session.close()

        thread = threading.Thread(target=run_extract, daemon=True)
        thread.start()
        time.sleep(0.4)
        assert thread.is_alive(), (
            "extract should still be blocked on the document row lock"
        )

        holder.commit()
        thread.join(timeout=20)
        assert not thread.is_alive()

        assert "result" not in outcomes
        error = outcomes.get("error")
        assert isinstance(error, DocumentAlreadyExtractedError)
        assert error.status_code == 409

        verifier = Session(engine)
        try:
            record_count = len(
                list(
                    verifier.scalars(
                        select(DocumentProcessingRecord.id).where(
                            DocumentProcessingRecord.document_id
                            == document.id
                        )
                    ).all()
                )
            )
            chunk_count = len(
                list(
                    verifier.scalars(
                        select(DocumentChunk.id).where(
                            DocumentChunk.document_id == document.id
                        )
                    ).all()
                )
            )
        finally:
            verifier.close()
        assert record_count == 1
        assert chunk_count == 0
    finally:
        holder.close()
        if thread is not None:
            thread.join(timeout=20)


@pytest.mark.integration
def test_two_simultaneous_extracts_yield_single_completed_run(
    engine,
    committed_document: SimpleNamespace,
) -> None:
    document = committed_document.document
    barrier = threading.Barrier(2, timeout=15)
    outcomes: dict[int, object] = {}
    threads: list[threading.Thread] = []

    def run_extract(index: int) -> None:
        session = Session(engine, expire_on_commit=False)
        try:
            barrier.wait()
            outcomes[index] = committed_document.service.extract(
                session, document
            )
        except BaseException as exc:
            outcomes[index] = exc
        finally:
            session.close()

    try:
        for index in range(2):
            thread = threading.Thread(
                target=run_extract,
                args=(index,),
                daemon=True,
            )
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        for thread in threads:
            assert not thread.is_alive(), "extract thread did not finish"

        assert len(outcomes) == 2
        successes = [
            outcome
            for outcome in outcomes.values()
            if isinstance(outcome, ExtractionRunResult)
        ]
        rejections = [
            outcome
            for outcome in outcomes.values()
            if isinstance(outcome, DocumentAlreadyExtractedError)
        ]
        assert len(successes) == 1, (
            f"expected exactly one success, got outcomes={outcomes!r}"
        )
        assert len(rejections) == 1, (
            f"expected exactly one rejection, got outcomes={outcomes!r}"
        )
        assert rejections[0].code == "document_already_extracted"

        verifier = Session(engine)
        try:
            records = list(
                verifier.scalars(
                    select(DocumentProcessingRecord).where(
                        DocumentProcessingRecord.document_id == document.id
                    )
                ).all()
            )
            chunks = list(
                verifier.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == document.id
                    )
                ).all()
            )
        finally:
            verifier.close()

        assert len(records) == 1
        assert records[0].processing_status == "completed"
        assert records[0].error_message is None
        assert len(chunks) == successes[0].chunk_count
        assert len(chunks) >= 1
    finally:
        for thread in threads:
            thread.join(timeout=30)

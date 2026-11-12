from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models import Credential, CredentialDocument
from app.schemas import (
    DocumentExtractionResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.services.document_extraction import (
    DocumentExtractionService,
    get_document_extraction_service,
)
from app.services.document_storage import (
    DocumentStorageError,
    InvalidStorageKeyError,
    PrivateDocumentStorage,
    get_document_storage,
)
from app.services.document_validation import DocumentUploadError
from app.services.pdf_extraction import PDFExtractionError

logger = logging.getLogger(__name__)

DEVELOPMENT_ONLY_WARNING = (
    "Development-only endpoint: authentication and therapist-level ownership "
    "checks are not implemented. Do not expose this endpoint in production "
    "without authorization."
)

router = APIRouter(tags=["documents"])


def _delete_saved_file(
    storage: PrivateDocumentStorage,
    storage_key: str,
) -> None:
    try:
        storage.delete(storage_key)
    except (DocumentStorageError, InvalidStorageKeyError):
        logger.warning("Failed to remove a saved upload during cleanup.")


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a credential PDF (development only)",
    description=(
        "Validate and store a PDF for an existing credential. "
        f"{DEVELOPMENT_ONLY_WARNING}"
    ),
)
def upload_document(
    credential_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[
        PrivateDocumentStorage, Depends(get_document_storage)
    ],
) -> DocumentUploadResponse:
    credential = db.scalar(
        select(Credential)
        .options(joinedload(Credential.therapist_profile))
        .where(Credential.id == credential_id)
    )
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found.",
        )

    therapist_profile = credential.therapist_profile
    if therapist_profile is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Credential has no associated therapist profile.",
        )

    try:
        stored_document = storage.save_pdf(
            file.file,
            declared_content_type=file.content_type,
            original_filename=file.filename,
        )
    except DocumentUploadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    except DocumentStorageError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    document = CredentialDocument(
        credential_id=credential.id,
        uploaded_by_user_id=therapist_profile.user_id,
        original_filename=stored_document.original_filename,
        storage_key=stored_document.storage_key,
        content_type=stored_document.content_type,
        file_size_bytes=stored_document.file_size_bytes,
        sha256_checksum=stored_document.sha256_checksum,
    )

    try:
        db.add(document)
        db.flush()
        db.refresh(document)
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.warning("Failed to roll back a document metadata write.")
        _delete_saved_file(storage, stored_document.storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document metadata could not be saved.",
        ) from exc

    return DocumentUploadResponse(
        document=DocumentResponse.model_validate(document)
    )


@router.post(
    "/documents/{document_id}/extract",
    response_model=DocumentExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Extract PDF text and OCR pages (development only)",
    description=(
        "Synchronously extract embedded text and OCR low-text pages. "
        "Extracted text is stored privately; only aggregate metadata is "
        f"returned. {DEVELOPMENT_ONLY_WARNING}"
    ),
)
def extract_document(
    document_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    extraction_service: Annotated[
        DocumentExtractionService,
        Depends(get_document_extraction_service),
    ],
) -> DocumentExtractionResponse:
    document = db.get(CredentialDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    try:
        result = extraction_service.extract(db, document)
    except PDFExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    record = result.processing_record
    return DocumentExtractionResponse(
        document_id=document.id,
        processing_record_id=record.id,
        processing_status=record.processing_status,
        page_count=result.page_count,
        ocr_page_count=result.ocr_page_count,
        chunk_count=result.chunk_count,
        processed_at=record.processed_at,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Get document metadata (development only)",
    description=DEVELOPMENT_ONLY_WARNING,
)
def get_document_metadata(
    document_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentResponse:
    document = db.get(CredentialDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return DocumentResponse.model_validate(document)


@router.get(
    "/credentials/{credential_id}/documents",
    response_model=DocumentListResponse,
    summary="List credential document metadata (development only)",
    description=DEVELOPMENT_ONLY_WARNING,
)
def list_credential_documents(
    credential_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentListResponse:
    credential_exists = db.scalar(
        select(Credential.id).where(Credential.id == credential_id)
    )
    if credential_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found.",
        )

    documents = list(
        db.scalars(
            select(CredentialDocument)
            .where(CredentialDocument.credential_id == credential_id)
            .order_by(
                CredentialDocument.uploaded_at.desc(),
                CredentialDocument.id.asc(),
            )
        ).all()
    )

    return DocumentListResponse(
        credential_id=credential_id,
        count=len(documents),
        documents=[
            DocumentResponse.model_validate(document)
            for document in documents
        ],
    )

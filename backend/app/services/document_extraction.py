from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import (
    CredentialDocument,
    DocumentChunk,
    DocumentProcessingRecord,
)
from app.services.document_storage import (
    InvalidStorageKeyError,
    PrivateDocumentStorage,
    get_document_storage,
)
from app.services.extracted_text_storage import (
    ExtractedTextStorageError,
    InvalidExtractedTextKeyError,
    PrivateExtractedTextStorage,
    get_extracted_text_storage,
)
from app.services.pdf_extraction import (
    DocumentAlreadyExtractedError,
    ExtractionStorageError,
    PDFExtractionError,
    PDFExtractionResult,
    PDFTextExtractor,
    SourceDocumentMissingError,
    get_pdf_text_extractor,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionRunResult:
    processing_record: DocumentProcessingRecord
    page_count: int
    ocr_page_count: int
    chunk_count: int


class DocumentExtractionService:
    def __init__(
        self,
        document_storage: PrivateDocumentStorage,
        extracted_text_storage: PrivateExtractedTextStorage,
        extractor: PDFTextExtractor,
        *,
        chunk_size_chars: int = 2000,
        chunk_overlap_chars: int = 200,
    ) -> None:
        if chunk_size_chars <= 0:
            raise ValueError("chunk_size_chars must be positive")
        if chunk_overlap_chars < 0:
            raise ValueError("chunk_overlap_chars must be non-negative")
        if chunk_overlap_chars >= chunk_size_chars:
            raise ValueError(
                "chunk_overlap_chars must be smaller than chunk_size_chars"
            )

        self.document_storage = document_storage
        self.extracted_text_storage = extracted_text_storage
        self.extractor = extractor
        self.chunk_size_chars = chunk_size_chars
        self.chunk_overlap_chars = chunk_overlap_chars

    def extract(
        self,
        db: Session,
        document: CredentialDocument,
    ) -> ExtractionRunResult:
        self._ensure_document_is_extractable(db, document)
        source_path = self._source_path(document)

        record = DocumentProcessingRecord(
            document_id=document.id,
            model_name=self.extractor.model_name,
            model_version=self.extractor.model_version,
            predicted_category=None,
            confidence_score=None,
            processing_status="processing",
            extracted_text_reference=None,
            error_message=None,
            processed_at=None,
        )
        try:
            db.add(record)
            db.commit()
            db.refresh(record)
        except SQLAlchemyError as exc:
            db.rollback()
            raise PDFExtractionError() from exc

        try:
            result = self.extractor.extract_file(source_path)
        except PDFExtractionError as exc:
            self._mark_failed(db, record, exc.code)
            raise
        except Exception as exc:
            self._mark_failed(db, record, "extraction_failed")
            raise PDFExtractionError() from exc

        artifact_reference: str | None = None
        try:
            artifact_reference = self.extracted_text_storage.save_json(
                self._artifact_payload(document, result)
            )
            chunks = self._build_chunks(document, result)
            if not chunks:
                raise PDFExtractionError("The PDF contains no usable text.")

            now = datetime.now(timezone.utc)
            record.processing_status = "completed"
            record.extracted_text_reference = artifact_reference
            record.error_message = None
            record.processed_at = now
            db.add_all(chunks)
            db.commit()
            db.refresh(record)
        except PDFExtractionError:
            db.rollback()
            self._delete_artifact(artifact_reference)
            self._mark_failed(db, record, "no_extractable_text")
            raise
        except ExtractedTextStorageError as exc:
            db.rollback()
            self._mark_failed(db, record, "extraction_storage_failed")
            raise ExtractionStorageError() from exc
        except SQLAlchemyError as exc:
            db.rollback()
            self._delete_artifact(artifact_reference)
            self._mark_failed(db, record, "extraction_storage_failed")
            raise ExtractionStorageError() from exc
        except Exception as exc:
            db.rollback()
            self._delete_artifact(artifact_reference)
            self._mark_failed(db, record, "extraction_failed")
            raise PDFExtractionError() from exc

        return ExtractionRunResult(
            processing_record=record,
            page_count=result.page_count,
            ocr_page_count=result.ocr_page_count,
            chunk_count=len(chunks),
        )

    def _ensure_document_is_extractable(
        self,
        db: Session,
        document: CredentialDocument,
    ) -> None:
        locked_document_id = db.scalar(
            select(CredentialDocument.id)
            .where(CredentialDocument.id == document.id)
            .with_for_update()
        )
        if locked_document_id is None:
            raise SourceDocumentMissingError()

        existing_chunk = db.scalar(
            select(DocumentChunk.id)
            .where(DocumentChunk.document_id == document.id)
            .limit(1)
        )
        if existing_chunk is not None:
            raise DocumentAlreadyExtractedError()

        in_progress = db.scalar(
            select(DocumentProcessingRecord.id)
            .where(
                DocumentProcessingRecord.document_id == document.id,
                DocumentProcessingRecord.processing_status == "processing",
            )
            .limit(1)
        )
        if in_progress is not None:
            raise DocumentAlreadyExtractedError(
                "This document is already being extracted."
            )

    def _source_path(self, document: CredentialDocument) -> Path:
        try:
            source_path = self.document_storage.path_for(
                document.storage_key
            )
        except InvalidStorageKeyError as exc:
            raise SourceDocumentMissingError() from exc

        if not source_path.is_file():
            raise SourceDocumentMissingError()
        return source_path

    def _artifact_payload(
        self,
        document: CredentialDocument,
        result: PDFExtractionResult,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "document_id": str(document.id),
            "sha256_checksum": document.sha256_checksum,
            "extractor": self.extractor.model_name,
            "extractor_version": self.extractor.model_version,
            "page_count": result.page_count,
            "ocr_page_count": result.ocr_page_count,
            "text_bytes": result.text_bytes,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pages": [page.to_dict() for page in result.pages],
        }

    def _build_chunks(
        self,
        document: CredentialDocument,
        result: PDFExtractionResult,
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        chunk_index = 0

        for page in result.pages:
            for text, char_start, char_end in self._chunk_text(page.text):
                chunks.append(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_index=chunk_index,
                        chunk_text=text,
                        page_number=page.page_number,
                        embedding=None,
                        embedding_model=None,
                        chunk_metadata={
                            "page_number": page.page_number,
                            "extraction_method": page.extraction_method,
                            "ocr_confidence": page.ocr_confidence,
                            "char_start": char_start,
                            "char_end": char_end,
                            "source_sha256": document.sha256_checksum,
                        },
                    )
                )
                chunk_index += 1

        return chunks

    def _chunk_text(self, text: str) -> list[tuple[str, int, int]]:
        if not text.strip():
            return []

        chunks: list[tuple[str, int, int]] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(start + self.chunk_size_chars, text_length)
            if end < text_length:
                boundary = max(
                    text.rfind("\n", start, end),
                    text.rfind(" ", start, end),
                )
                if boundary > start + self.chunk_size_chars // 2:
                    end = boundary

            chunk = text[start:end].strip()
            if chunk:
                chunks.append((chunk, start, end))

            if end >= text_length:
                break

            next_start = max(0, end - self.chunk_overlap_chars)
            if next_start <= start:
                next_start = end
            start = next_start

        return chunks

    def _mark_failed(
        self,
        db: Session,
        record: DocumentProcessingRecord,
        error_code: str,
    ) -> None:
        try:
            if getattr(record, "id", None) is None:
                return
            stored_record = db.get(DocumentProcessingRecord, record.id)
            if stored_record is None:
                return
            stored_record.processing_status = "failed"
            stored_record.error_message = error_code
            stored_record.processed_at = datetime.now(timezone.utc)
            db.commit()
        except SQLAlchemyError:
            try:
                db.rollback()
            except SQLAlchemyError:
                logger.warning(
                    "Failed to roll back a failed extraction record."
                )

    def _delete_artifact(self, storage_key: str | None) -> None:
        if storage_key is None:
            return
        try:
            self.extracted_text_storage.delete(storage_key)
        except (ExtractedTextStorageError, InvalidExtractedTextKeyError):
            logger.warning(
                "Failed to remove an extracted-text artifact during cleanup."
            )


def get_document_extraction_service(
    document_storage: Annotated[
        PrivateDocumentStorage, Depends(get_document_storage)
    ],
    extracted_text_storage: Annotated[
        PrivateExtractedTextStorage,
        Depends(get_extracted_text_storage),
    ],
    extractor: Annotated[
        PDFTextExtractor, Depends(get_pdf_text_extractor)
    ],
) -> DocumentExtractionService:
    from app.core.config import settings

    return DocumentExtractionService(
        document_storage,
        extracted_text_storage,
        extractor,
        chunk_size_chars=settings.pdf_extraction_chunk_size_chars,
        chunk_overlap_chars=settings.pdf_extraction_chunk_overlap_chars,
    )

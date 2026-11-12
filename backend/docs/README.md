# Documentation

This directory contains the technical and project documentation for the Therapist Credential Verification Backend.

## Purpose

The `docs` directory is the single location for design notes, architecture references, operational guidance, and module-level documentation that supports the codebase. It keeps written documentation separate from source code so both can evolve cleanly.

## Documentation Planned for Future Modules

The following documentation is expected to be added in later modules:

- **Architecture overview:** high-level backend architecture, component responsibilities, and request flow.
- **Configuration guide:** environment variables, settings management, and environment-specific configuration.
- **Database documentation:** schema design, entity relationships, SQLAlchemy models, and Alembic migration workflow.
- **API reference:** endpoint descriptions, request/response schemas, status codes, and error formats.
- **Authentication and authorization:** account model, token issuance, ownership checks, and access control rules.
- **Document processing and classification:** classification pipeline, embeddings, and RAG behavior (PDF extraction and OCR are documented above).
- **Credential verification workflow:** verification states, processing rules, and background job behavior.
- **Testing guide:** how to run Pytest, test organization, and coverage expectations.
- **Deployment and operations:** local setup, environment provisioning, and operational runbooks.

## Current Status

Module 7B adds the PDF text extraction and OCR foundation. SQLAlchemy engine, session, declarative base, and model metadata registration are available in `app/db/`; models are under `app/models/`; Pydantic schemas are under `app/schemas/` (`document.py`, `extraction.py`); storage, validation, extraction, and extracted-text services are under `app/services/` (`document_storage.py`, `document_validation.py`, `pdf_extraction.py`, `document_extraction.py`, `extracted_text_storage.py`); and migration configuration and revisions are under `alembic/`.

The migrations have been applied to the `therapist_credentials` development database. The eight application tables are `users`, `therapist_profiles`, `credentials`, `credential_documents`, `verification_records`, `audit_logs`, `document_processing_records`, and `document_chunks`. PostgreSQL pgvector 0.8.7 is enabled, and `document_chunks.embedding` stores optional variable-dimension vector embeddings. Modules 6 and 7B add no tables or migrations; extraction confidence and page metadata are stored in artifact and chunk JSON, not in new columns.

## Document Ingestion API

The following development-only routes are available under `/api/v1`:

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/documents` | Upload one PDF for an existing credential. |
| GET | `/documents/{document_id}` | Retrieve safe metadata for one document. |
| GET | `/credentials/{credential_id}/documents` | List safe metadata for a credential's documents. |
| POST | `/documents/{document_id}/extract` | Synchronously extract text and OCR low-text pages. |

Supported content is PDF only. The default maximum upload size is 10 MiB and is configurable with `DOCUMENT_MAX_UPLOAD_SIZE_BYTES`. The allowed declared content types are configurable with `DOCUMENT_ALLOWED_CONTENT_TYPES`; Module 6 still validates the actual `%PDF-` signature.

Because authentication is not implemented, the uploader is derived from the credential's therapist profile owner rather than from a client-supplied user ID.

Uploads are streamed in bounded chunks to `DOCUMENT_STORAGE_DIR`, which defaults to `storage/private_documents` inside the project and is excluded from Git. Storage keys are random and independent of the original filename. The database stores metadata only, not file binaries. SHA-256 checksums are calculated during streaming. Partial files are removed on handled failures, including database failures after a file has been saved.

API responses expose document ID, credential ID, original filename, normalized content type, file size, SHA-256 checksum, and upload time. They deliberately omit the storage key and filesystem path. There is no document download or public static-file route.

## Document Extraction API

`POST /documents/{document_id}/extract` runs extraction synchronously and returns `201` with aggregate metadata only: `message`, `document_id`, `processing_record_id`, `processing_status`, `page_count`, `ocr_page_count`, `chunk_count`, and `processed_at`. Extracted text, storage keys, filesystem paths, artifact filenames, and OCR confidence values are never returned.

Embedded text is parsed with PyMuPDF. Pages below `pdf_extraction_ocr_min_chars` (default 50) characters of embedded text are rendered at `pdf_extraction_render_dpi` (default 200) DPI and processed with Tesseract OCR (English) through `tesserocr`, with a `pytesseract` CLI fallback. Each OCR page records a mean word-confidence value scaled to `0..1`, stored in the private artifact JSON and per-chunk metadata. Text is split into page-aware chunks (`pdf_extraction_chunk_size_chars` default 2000, overlap `pdf_extraction_chunk_overlap_chars` default 200) with `embedding` left `NULL`.

Extracted text is written to `EXTRACTED_TEXT_STORAGE_DIR` (default `storage/private_extracted_text`) as a private JSON artifact keyed by a random 64-hex name, excluded from Git and deleted if chunk persistence fails. Each attempt appends a `document_processing_records` row moving `processing` to `completed` or `failed`; `predicted_category` and `confidence_score` remain `NULL` because classification is not implemented. Concurrent extraction is serialized by a `SELECT ... FOR UPDATE` row lock on the document: attempts while one is running or after completion return `409`. Failed attempts leave no chunks or artifacts and may be retried.

Limits and error statuses: `404` unknown document; `409` missing source file or already/in-progress extraction; `413` over `pdf_extraction_max_pages` (30) or `pdf_extraction_max_text_bytes` (2 MiB); `422` corrupted, encrypted, empty, or no-extractable-text PDFs; `503` OCR unavailable; `504` OCR exceeded `pdf_extraction_ocr_page_timeout_seconds` (30); `500` OCR or artifact storage failure.

OCR caveats: OCR is best-effort and depends on scan quality; confidence values are heuristics, not proof of accuracy; only English language data is configured; a page with no OCR words records `ocr_confidence: null`; extraction never validates credential authenticity.

## Security Limitations

- Authentication and therapist-level ownership checks are not implemented. These routes must not be exposed in production without authorization.
- PDF signature validation is a basic format check and does not prove that a file is valid, complete, harmless, or malware-free.
- Client-provided MIME types and filenames are not trusted as storage paths; storage keys are generated internally and path traversal is rejected.
- PDF parsing, OCR, and text extraction are implemented; embedding generation, classification execution, RAG retrieval/answer generation, malware scanning, verification workflow execution, and AI integration are not implemented yet.
- OCR output and confidence values are derived heuristics. They are not evidence that a credential is authentic, and extracted text must not be treated as verified content.
- Extracted text artifacts are stored privately and are never served by any endpoint; the artifact directory is excluded from Git.
- File contents, extracted text, and sensitive document data must not be logged.

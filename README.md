# Therapist Credential Verification Backend

## Project Name

Therapist Credential Verification Backend (`THERAPIST-CREDENTIAL-BACKEND-OPENCODE`).

## Project Purpose

This project provides the backend services for a Therapist Credential Verification System. Its goal is to support the secure submission, storage, classification, and verification of therapist credentials and related documents.

## Current Development Stage

**Module 7B: PDF Text Extraction and OCR Foundation.**

Module 1 created the project directory structure and base configuration files. Module 2 added a small, runnable FastAPI application with settings, health and information routes, automatic API documentation, and automated tests. Module 3 added the PostgreSQL database foundation. Module 4 added the six foundational credential-verification models and their first migration. Module 5A confirmed the PostgreSQL 18.6 environment and pgvector availability. Module 5B added document-processing/RAG tables and pgvector integration. Module 6 added document response schemas, PDF-only validation, private local file storage, a credential document upload endpoint, metadata retrieval and credential-scoped listing. Module 7B adds synchronous PDF text extraction with Tesseract OCR fallback, page-aware chunk persistence, private extracted-text artifact storage, per-page OCR confidence metadata, concurrency protection, and the `POST /api/v1/documents/{document_id}/extract` endpoint with a comprehensive test suite.

> **Important:** Document upload, metadata, and extraction endpoints are **development-only**. Authentication, authorization, and therapist-level ownership checks have not been implemented. Do not expose these endpoints in production. PDF signature checks are a basic file-format check and do **not** prove that a file is harmless. Embedding generation, AI classification execution, RAG retrieval/answer generation, malware scanning, and document downloads have **not** been implemented. OCR results and confidence values are best-effort heuristics, not proof of extraction accuracy. AI classification is not proof of credential authenticity or validity.

## Python Version

Python 3.13

## Technology Stack

### Currently in use

- Python 3.13
- FastAPI
- Uvicorn
- Pydantic Settings
- SQLAlchemy
- Psycopg 3 (`psycopg[binary]`)
- Alembic
- PostgreSQL
- pgvector 0.8.7 (PostgreSQL extension)
- `pgvector` Python SQLAlchemy integration
- `python-multipart` (multipart form uploads)
- PyMuPDF (`pymupdf`) for PDF parsing and page rendering
- Tesseract OCR 5 (via `tesserocr`, with `pytesseract` CLI fallback)
- Pillow (synthetic test images and OCR rendering)
- Pytest
- HTTPX (used by FastAPI's test client)

## Prerequisites

- Python 3.13 installed and available on `PATH`.
- PowerShell on Windows.

## Windows PowerShell Virtual Environment Activation

The virtual environment is created and maintained separately by the developer. From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If script execution is blocked by policy, activate for the current session with:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

To deactivate:

```powershell
deactivate
```

## Dependency Installation

With the virtual environment activated:

```powershell
python -m pip install -r requirements.txt
```

The project declares these dependencies (exact versions are pinned in `requirements.txt`):

- `fastapi`
- `uvicorn[standard]`
- `pydantic-settings`
- `sqlalchemy`
- `psycopg[binary]`
- `alembic`
- `pgvector`
- `python-multipart`
- `pymupdf`
- `pytesseract`
- `pillow`
- `tesserocr`
- `pytest`
- `httpx`

## Running the Application

With the virtual environment activated, from the project root:

```powershell
python -m uvicorn app.main:app --reload
```

The server starts at `http://127.0.0.1:8000` by default.

## Local Endpoints

| Method | Path | Description | Expected Response |
| --- | --- | --- | --- |
| GET | `/` | Welcome message and running status | `{"message": "Welcome to the <application name>", "status": "running"}` |
| GET | `/health` | Application liveness check only | `{"status": "healthy", "application": "<application name>", "environment": "<environment>"}` |
| GET | `/api/v1/info` | Application information | Application name, environment, version `1.0.0`, and project description |
| POST | `/api/v1/documents` | Upload a PDF for an existing credential | Safe document metadata, `201 Created` |
| GET | `/api/v1/documents/{document_id}` | Retrieve one document's safe metadata | Safe document metadata |
| GET | `/api/v1/credentials/{credential_id}/documents` | List metadata for a credential's documents | Credential ID, count, and document metadata list |
| POST | `/api/v1/documents/{document_id}/extract` | Synchronously extract text and OCR low-text pages | Aggregate extraction metadata, `201 Created` |
| GET | `/docs` | Swagger UI API documentation | Interactive documentation page |
| GET | `/redoc` | ReDoc API documentation | Reference documentation page |

The `/health` endpoint reports only that the application process is alive. It does **not** verify a database, AI model, or any external service.

## Document Upload API (Development Only)

> **Authorization warning:** Authentication and therapist-level ownership checks are not implemented yet. These endpoints are for local development only and must not be exposed in production until authorization is added.

The upload endpoint accepts `multipart/form-data` with:

- `credential_id`: UUID of an existing credential.
- `file`: one PDF file.

Because authentication is not implemented, the document's `uploaded_by_user_id` is derived from the credential's therapist profile owner. The request does not accept an arbitrary uploader ID.

Only `application/pdf` is supported initially. The default maximum size is **10 MiB** (`10485760` bytes). Server-side validation checks the declared content type, non-empty content, the actual `%PDF-` signature, and the streamed byte count. Files are written to private storage in bounded chunks and are hashed with SHA-256 while being streamed.

Example upload with `curl.exe`:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/documents" `
  -F "credential_id=11111111-1111-1111-1111-111111111111" `
  -F "file=@C:\path\to\credential.pdf;type=application/pdf"
```

Example response:

```json
{
  "message": "Document uploaded successfully.",
  "document": {
    "original_filename": "credential.pdf",
    "content_type": "application/pdf",
    "file_size_bytes": 48213,
    "sha256_checksum": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "id": "22222222-2222-2222-2222-222222222222",
    "credential_id": "11111111-1111-1111-1111-111111111111",
    "uploaded_at": "2026-11-04T12:00:00+00:00"
  }
}
```

Retrieve metadata:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/documents/22222222-2222-2222-2222-222222222222"
```

List a credential's documents:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/credentials/11111111-1111-1111-1111-111111111111/documents"
```

### Private Storage Behavior

- Files are stored under `DOCUMENT_STORAGE_DIR`, defaulting to `storage/private_documents` inside the project.
- The default storage directory is excluded by `.gitignore`. Never commit uploaded documents.
- Storage keys are random generated names; the original filename is retained only as database metadata.
- Original filenames are reduced to a safe basename, so path components are not used as filesystem paths.
- File creation uses exclusive creation to avoid overwriting existing files, and partially written files are removed on handled errors.
- The database stores metadata and a storage key only, never document binaries.
- API responses omit the internal storage key and filesystem paths.
- There is **no** document download or public static-file route in this module.

### Security Limitations

- No authentication, authorization, ownership enforcement, rate limiting, malware scanning, or content disarm is implemented.
- PDF signature validation confirms only that the file starts with the PDF header. It does **not** prove the file is valid, harmless, or complete.
- Filename extension and client-declared MIME type are not trusted on their own, but they are not a substitute for malware scanning.
- Do not log file contents or uploaded document data.
- Do not expose the development upload, metadata, or listing routes in production without authorization and therapist-level access control.

## Document Extraction API (Development Only)

> **Authorization warning:** the extraction endpoint has no authentication or ownership checks. It is for local development only and must not be exposed in production until authorization is added.

`POST /api/v1/documents/{document_id}/extract` runs extraction synchronously in the request. It requires no request body. The document must already exist and its stored source file must be present.

Example:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/documents/22222222-2222-2222-2222-222222222222/extract"
```

Success response (`201 Created`) — aggregate metadata only:

```json
{
  "message": "Document extraction completed.",
  "document_id": "22222222-2222-2222-2222-222222222222",
  "processing_record_id": "33333333-3333-3333-3333-333333333333",
  "processing_status": "completed",
  "page_count": 2,
  "ocr_page_count": 1,
  "chunk_count": 3,
  "processed_at": "2026-11-12T10:00:00+00:00"
}
```

The response never contains extracted text, storage keys, filesystem paths, artifact filenames, or OCR confidence values.

### Extraction Behavior

- Embedded text is read first with PyMuPDF. Pages whose embedded text is below `pdf_extraction_ocr_min_chars` (default 50 characters) are rendered at `pdf_extraction_render_dpi` (default 200 DPI) and processed with Tesseract OCR (English, `eng.traineddata`).
- OCR runs through `tesserocr` (bundled Tesseract 5) when available, with a `pytesseract` CLI fallback. If neither backend can run, extraction fails with `503`.
- Each OCR page records a confidence value: the mean of Tesseract word confidences scaled to `0..1`, rounded to four decimals. It is stored in the private extracted-text artifact and in per-chunk metadata JSON. It is not a database column and is not returned by the API.
- Extracted text is split into page-aware chunks of `pdf_extraction_chunk_size_chars` (default 2000) characters with `pdf_extraction_chunk_overlap_chars` (default 200) characters of overlap. Chunk embeddings remain `NULL`; no embedding model runs in this module.
- Extracted text is written to a private artifact JSON file under `EXTRACTED_TEXT_STORAGE_DIR` (default `storage/private_extracted_text`) with a random 64-hex-character `.json` key. The artifact is excluded from Git, is never returned by any endpoint, and is deleted if chunk persistence fails.
- A `document_processing_records` row tracks each attempt: status moves `processing` → `completed` or `failed`, with `model_name`/`model_version` set and `predicted_category`/`confidence_score` left `NULL` (classification is not implemented). Failed attempts leave no chunks and no artifact, and can be retried; every attempt appends a new record.
- Concurrent extraction is serialized with a row lock (`SELECT ... FOR UPDATE`) on the document. A second attempt while one is running, or after one completed, returns `409`.

### Extraction Limits and Error Statuses

| Status | Condition | `detail` |
| --- | --- | --- |
| `404` | Document ID not found | `Document not found.` |
| `409` | Stored source file missing | `The stored document file is unavailable.` |
| `409` | Already extracted, or extraction in progress | `This document has already been extracted.` / `This document is already being extracted.` |
| `413` | More than `pdf_extraction_max_pages` (default 30) pages | `The PDF exceeds the maximum allowed page count.` |
| `413` | More than `pdf_extraction_max_text_bytes` (default 2 MiB) extracted | `The PDF exceeds the maximum allowed extracted text size.` |
| `422` | File cannot be opened or parsed | `The PDF could not be opened or parsed.` |
| `422` | Password-protected PDF | `Encrypted PDFs cannot be processed.` |
| `422` | PDF has no pages | `The PDF contains no pages.` |
| `422` | No usable text after extraction | `The PDF contains no extractable text.` |
| `503` | OCR required but no OCR backend available | `OCR is required but the OCR engine is unavailable.` |
| `504` | OCR exceeded `pdf_extraction_ocr_page_timeout_seconds` (default 30) | `OCR exceeded the allowed processing time.` |
| `500` | OCR processing failure, or extracted-text storage failure | `OCR processing failed.` / `The extracted text could not be stored.` |

### OCR Caveats

- OCR is best-effort. Scanned documents depend on image quality; confidence values are heuristics and do not prove that recognized text is correct.
- Only English language data is configured (`tesseract_language`, default `eng`).
- A page that yields no OCR words is recorded with `ocr_confidence: null`.
- Extraction failures never leave partial chunks or artifacts behind, but a failed record remains for audit and may be retried.
- Extracted text, OCR confidence, and any future AI classification are derived data. They do not establish that a credential is authentic or valid.

## Running Tests

With the virtual environment activated, from the project root:

```powershell
python -m pytest -v
```

The test suite includes FastAPI route tests, mocked SQLAlchemy tests, model metadata tests in `tests/test_models.py`, document-processing/RAG model tests in `tests/test_document_models.py`, document API/storage tests in `tests/test_documents_api.py`, migration/development-database tests in `tests/test_migrations.py`, and Module 7B extraction tests:

- `tests/test_pdf_extraction.py` — extraction engine behavior: embedded text, OCR fallback, per-page OCR confidence, page/text limits, corrupted/encrypted/blank inputs, OCR unavailable/timeout/failure paths, and source-file integrity.
- `tests/test_document_extraction.py` — extraction service workflow: processing records, page-aware chunks, artifact lifecycle, status transitions, failure and retry behavior, extracted-text storage key safety, and simulated persistence failure cleanup.
- `tests/test_extraction_api.py` — the extract endpoint: success response shape (aggregate metadata only, no text or storage leakage), and `404`/`409`/`413`/`422`/`503`/`504` error mapping.
- `tests/test_extraction_concurrency.py` — row-lock concurrency: a held `SELECT ... FOR UPDATE` lock rejects a `NOWAIT` probe, blocks a concurrent extract until commit, and two simultaneous extracts yield exactly one completed run.
- `tests/synthetic_pdf.py` — shared synthetic PDF builders (embedded, scanned, mixed, blank, encrypted). Tests never use real credential documents.

`tests/test_migrations.py` verifies that the applied Alembic revision matches the current head, that the expected tables and constraints exist, and that the ORM metadata matches the development database.

Document constraint and upload integration tests create prerequisite rows inside database transactions and roll them back during teardown. Upload tests use isolated temporary directories and validate empty files, unsupported types, signatures, size limits, unique keys, checksums, database persistence, cleanup after simulated database failure, metadata retrieval, credential-scoped listing, and storage-path leakage. Extraction service and API tests use the same transaction-rollback pattern with savepoint-isolated sessions; concurrency tests commit real rows and delete them in foreign-key order during teardown, leaving all application tables empty.

The live PostgreSQL connectivity test in `tests/test_database_postgres.py` is marked `integration` and is skipped unless `TEST_DATABASE_URL` is set. It only runs `SELECT 1`; it does not create tables. To run the entire suite including that connectivity test, set `TEST_DATABASE_URL` to the local development database URL and run:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<username>:<password>@<host>:<port>/<database_name>"
python -m pytest -v
```

To run everything except live integrations:

```powershell
python -m pytest -v -m "not integration"
```

## Configuration

Application settings are defined in `app/core/config.py` using `pydantic-settings`. Settings are read from environment variables and an optional `.env` file, with case-insensitive variable names.

Available settings:

| Variable | Default |
| --- | --- |
| `APP_NAME` | `Therapist Credential Verification API` |
| `APP_ENV` | `development` |
| `DEBUG` | `true` |
| `API_V1_PREFIX` | `/api/v1` |
| `DATABASE_URL` | `postgresql+psycopg://postgres@localhost:5432/therapist_credentials` |
| `DOCUMENT_STORAGE_DIR` | `<project root>/storage/private_documents` |
| `DOCUMENT_MAX_UPLOAD_SIZE_BYTES` | `10485760` (10 MiB) |
| `DOCUMENT_ALLOWED_CONTENT_TYPES` | `application/pdf` |
| `EXTRACTED_TEXT_STORAGE_DIR` | `<project root>/storage/private_extracted_text` |
| `pdf_extraction_max_pages` | `30` |
| `pdf_extraction_max_text_bytes` | `2097152` (2 MiB) |
| `pdf_extraction_ocr_min_chars` | `50` |
| `pdf_extraction_render_dpi` | `200` |
| `pdf_extraction_ocr_page_timeout_seconds` | `30.0` |
| `pdf_extraction_ocr_total_timeout_seconds` | `300.0` |
| `pdf_extraction_chunk_size_chars` | `2000` |
| `pdf_extraction_chunk_overlap_chars` | `200` |
| `tesseract_language` | `eng` |
| `tesseract_data_dir` | `<project root>/storage/tessdata` |

Copy `.env.example` to `.env` to override values locally. Add your PostgreSQL password to `DATABASE_URL` in the local `.env` file. Never commit a real `.env` file or real secrets. `DOCUMENT_ALLOWED_CONTENT_TYPES` is a comma-separated declared content-type list; Module 6 still validates PDF signatures and supports PDF uploads only.

## Database Setup

SQLAlchemy configuration lives in `app/db/session.py`. The declarative base is defined in `app/db/base_class.py` and registered with all model metadata in `app/db/base.py`. Alembic configuration lives in `alembic.ini` and `alembic/env.py`, and reads `DATABASE_URL` from the application settings.

The migrations create the eight application tables listed below. Alembic's own `alembic_version` table is also present. Module 5B requires the PostgreSQL `vector` extension for `document_chunks.embedding`; pgvector 0.8.7 is installed and enabled in the development database.

## Database Models

| Model | Table | Purpose |
| --- | --- | --- |
| `User` | `users` | Account identity, role, active status, and optional temporary password hash placeholder. |
| `TherapistProfile` | `therapist_profiles` | Professional profile for an applicant, one-to-one with a user. |
| `Credential` | `credentials` | Credential claims such as degrees, licenses, training, supervision, and other records. |
| `CredentialDocument` | `credential_documents` | Metadata and storage references for supporting credential files. |
| `VerificationRecord` | `verification_records` | Append-only review history for a credential. |
| `AuditLog` | `audit_logs` | Append-only audit events with optional actor and JSON details. |
| `DocumentProcessingRecord` | `document_processing_records` | Append-only AI/OCR processing attempts, predicted category, confidence, status, and errors. |
| `DocumentChunk` | `document_chunks` | Text chunks for future retrieval-augmented generation, with optional variable-dimension pgvector embeddings. |

All eight tables use UUID primary keys and timezone-aware timestamps. No plaintext passwords or document binary content are stored by these models. Processing predictions and embeddings are derived data; they do not establish that a credential is authentic.

### Model Relationships

```text
User 1--0..1 TherapistProfile 1--* Credential
User 1--* CredentialDocument (uploaded_by_user_id)
User 1--* VerificationRecord (reviewer_user_id, optional)
User 1--* AuditLog (actor_user_id, optional)
Credential 1--* CredentialDocument
Credential 1--* VerificationRecord
CredentialDocument 1--* DocumentProcessingRecord
CredentialDocument 1--* DocumentChunk
```

- `User.email` is unique. The model normalizes email to lowercase when set through SQLAlchemy.
- `TherapistProfile.user_id` is unique, enforcing one profile per user.
- `Credential.credential_type` and `Credential.status` use database check constraints.
- `CredentialDocument.file_size_bytes` must be positive, and `storage_key` is unique.
- `VerificationRecord.status` uses a database check constraint and multiple records are allowed per credential.
- `AuditLog.details` uses PostgreSQL `JSONB`; audit logs are not exposed through public API endpoints.
- `DocumentProcessingRecord.predicted_category`, `processing_status`, and `confidence_score` use database check constraints. Multiple processing attempts may reference one document.
- `DocumentChunk` enforces non-negative `chunk_index`, positive `page_number` when present, and a unique `(document_id, chunk_index)` pair.
- `DocumentChunk.embedding` uses pgvector with no fixed dimension, so future embedding models with different vector sizes can be stored; an embedding requires `embedding_model`.
- Destructive cascades are not configured. Foreign keys use `RESTRICT` or `SET NULL` where appropriate to protect verification, processing, and audit history.

### Migration Commands

Generate a migration after changing model metadata:

```powershell
python -m alembic revision --autogenerate -m "describe change"
```

Apply all pending migrations:

```powershell
python -m alembic upgrade head
```

Inspect the current revision:

```powershell
python -m alembic current
```

Inspect database tables through SQLAlchemy:

```powershell
python -c "from sqlalchemy import create_engine, inspect; from app.core.config import settings; e = create_engine(settings.database_url); print(sorted(inspect(e).get_table_names(schema='public'))); e.dispose()"
```

Inspect database tables through PostgreSQL `psql`:

```powershell
psql -U postgres -d therapist_credentials -c "\dt"
```

Migration history:

- `07bb381933fc` – `create foundational credential verification schema`.
- `d177e5314c02` – `add document processing and RAG tables` (current head).

The Module 5B migration requires the `vector` extension. If setting up a fresh database, enable pgvector before upgrading:

```powershell
psql -U postgres -d therapist_credentials -c "CREATE EXTENSION IF NOT EXISTS vector;"
python -m alembic upgrade head
```

## Planned Backend Development Modules

1. **Module 1 – Backend Project Initialization:** directory structure and base configuration.
2. **Module 2 – FastAPI Backend Foundation:** runnable app, settings, basic routes, and tests.
3. **Module 3 – Database Setup:** PostgreSQL connection, SQLAlchemy configuration, and Alembic scaffolding.
4. **Module 4 – Database Models and Migrations:** six foundational ORM models, relationships, constraints, initial migration, and model/migration tests.
5. **Module 5A – PostgreSQL and pgvector Readiness:** environment inspection and pgvector extension verification.
6. **Module 5B – Document Processing and RAG Database Foundation:** processing-history and chunk tables, pgvector column, migration, and database constraint tests.
7. **Module 6 – API, Schemas and Document Ingestion Foundation:** document schemas, PDF validation, private local storage, upload, metadata retrieval, and credential-scoped listing.
8. **Module 7B – PDF Text Extraction and OCR Foundation** (current): PyMuPDF text extraction, Tesseract OCR fallback, per-page confidence, page-aware chunking, private extracted-text storage, the synchronous extraction endpoint, and extraction/concurrency tests.
9. **Module 8 – Authentication and Authorization:** user accounts, JWT, ownership checks, and access control.
10. **Module 9 – Document Processing and Classification:** classification execution, embeddings, and RAG retrieval over stored chunks.
11. **Module 10 – Credential Verification Workflow:** verification services and background processing.
12. **Module 11 – Testing and Quality Assurance:** expanded Pytest suites and coverage.

Module numbering may be refined in later planning documents.

## Project Structure

```text
THERAPIST-CREDENTIAL-BACKEND-OPENCODE/
|-- app/
|   |-- __init__.py
|   |-- main.py
|   |-- api/
|   |   |-- __init__.py
|   |   |-- routes/
|   |       |-- __init__.py
|   |       |-- documents.py
|   |       |-- health.py
|   |       |-- info.py
|   |-- core/
|   |   |-- __init__.py
|   |   |-- config.py
|   |-- db/
|   |   |-- __init__.py
|   |   |-- base.py
|   |   |-- base_class.py
|   |   |-- session.py
|   |-- models/
|   |   |-- __init__.py
|   |   |-- user.py
|   |   |-- therapist_profile.py
|   |   |-- credential.py
|   |   |-- credential_document.py
|   |   |-- verification_record.py
|   |   |-- audit_log.py
|   |   |-- document_processing_record.py
|   |   |-- document_chunk.py
|   |-- schemas/
|   |   |-- __init__.py
|   |   |-- document.py
|   |   |-- extraction.py
|   |-- services/
|       |-- __init__.py
|       |-- document_storage.py
|       |-- document_validation.py
|       |-- document_extraction.py
|       |-- extracted_text_storage.py
|       |-- pdf_extraction.py
|-- alembic/
|   |-- env.py
|   |-- script.py.mako
|   |-- versions/
|       |-- 07bb381933fc_create_foundational_credential_.py
|       |-- d177e5314c02_add_document_processing_and_rag_tables.py
|-- tests/
|   |-- __init__.py
|   |-- synthetic_pdf.py
|   |-- test_main.py
|   |-- test_database_mocked.py
|   |-- test_database_postgres.py
|   |-- test_models.py
|   |-- test_document_models.py
|   |-- test_documents_api.py
|   |-- test_migrations.py
|   |-- test_pdf_extraction.py
|   |-- test_document_extraction.py
|   |-- test_extraction_api.py
|   |-- test_extraction_concurrency.py
|-- docs/
|   |-- README.md
|-- storage/
|   |-- private_documents/   # local uploads, excluded from Git
|   |-- private_extracted_text/  # extracted-text artifacts, excluded from Git
|   |-- tessdata/            # Tesseract language data, excluded from Git
|-- .env.example
|-- .gitignore
|-- alembic.ini
|-- pytest.ini
|-- README.md
|-- requirements.txt
```

## Status

Module 7B PDF text extraction and OCR foundation complete. Synchronous extraction with PyMuPDF embedded-text parsing, Tesseract OCR fallback, per-page confidence metadata, page-aware chunking, private extracted-text artifact storage, failure/retry handling, row-lock concurrency protection, and the full extract endpoint error mapping are implemented. The eight application tables remain unchanged, with no new migration; OCR confidence and extraction metadata live in artifact/chunk JSON, not in new columns. Authentication, authorization, ownership checks, embedding generation, classification execution, RAG retrieval/answer generation, malware scanning, document downloads, verification workflow execution, and AI integration are not implemented yet.

# Therapist Credential Verification System

An AI-assisted platform for managing, processing and verifying therapist professional credentials.

This project combines a FastAPI backend, a frontend application and AI-powered document analysis components.

## 1. Repository Structure

```text
THERAPIST-CREDENTIAL-VERIFICATION-UNIFIED/
│
├── backend/       FastAPI backend and PostgreSQL integration
├── frontend/      Frontend application
├── ai/             AI classification, embeddings and RAG development
├── docs/           Project-wide technical documentation
├── README.md       Main project overview
└── .gitignore      Repository-wide ignore rules
```

## 2. Technology Stack

### Backend
- Python 3.13
- FastAPI
- PostgreSQL 18
- SQLAlchemy
- Alembic
- pgvector
- PyMuPDF
- Tesseract OCR
- Pytest

### Frontend
Frontend technology selection and implementation are maintained by the frontend team.

### AI
- Kaggle for experimentation and evaluation.
- Document classification.
- Text embeddings.
- Retrieval-Augmented Generation (RAG).
- PostgreSQL pgvector for vector storage and similarity search.

## 3. Current Backend Capabilities

- FastAPI application foundation.
- PostgreSQL connectivity.
- Database models and migrations.
- Therapist and credential data structures.
- Private PDF document ingestion.
- PDF text extraction.
- OCR fallback for scanned documents.
- Document processing records and text chunk storage.

## 4. AI Development Roadmap

- Inspect and evaluate the existing credential classifier.
- Develop an embedding pipeline.
- Build and evaluate RAG retrieval.
- Evaluate AI outputs and retrieval quality.
- Package suitable AI components for backend integration.

## 5. Development Setup

Refer to the backend README for backend installation and configuration instructions.

Backend API documentation during local development:

http://localhost:8000/docs

## 6. Project Development Rules

1. Backend, frontend and AI work should remain organized in their respective directories.
2. Coordinate shared API and database changes before integration.
3. Never commit passwords, API keys or environment files.
4. Never commit private therapist credentials or sensitive documents.
5. Do not upload large model files or datasets without an agreed storage strategy.
6. Update documentation when significant architecture or API changes are made.

## 7. Security and Verification Disclaimer

AI-assisted document analysis is intended to support the credential verification workflow. AI classification and RAG outputs do not independently establish that a professional credential is authentic. Appropriate human review and verification procedures remain necessary.

## 8. Project Status

The backend foundation and PDF extraction infrastructure have been implemented. Frontend development and AI/RAG development are being organized for subsequent integration.
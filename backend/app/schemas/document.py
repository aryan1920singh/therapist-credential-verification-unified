from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentMetadata(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    file_size_bytes: int = Field(gt=0)
    sha256_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentResponse(DocumentMetadata):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    credential_id: UUID
    uploaded_at: datetime


class DocumentUploadResponse(BaseModel):
    message: str = "Document uploaded successfully."
    document: DocumentResponse


class DocumentListResponse(BaseModel):
    credential_id: UUID
    count: int = Field(ge=0)
    documents: list[DocumentResponse]

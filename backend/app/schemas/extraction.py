from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentExtractionResponse(BaseModel):
    message: str = "Document extraction completed."
    document_id: UUID
    processing_record_id: UUID
    processing_status: str
    page_count: int = Field(ge=1)
    ocr_page_count: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    processed_at: datetime

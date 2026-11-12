from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.credential_document import CredentialDocument


class DocumentProcessingRecord(Base):
    __tablename__ = "document_processing_records"
    __table_args__ = (
        CheckConstraint(
            "predicted_category IS NULL OR "
            "predicted_category IN "
            "('degree', 'license', 'training', 'supervision', 'other')",
            name="ck_document_processing_records_predicted_category",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_document_processing_records_confidence_score_range",
        ),
        CheckConstraint(
            "processing_status IN "
            "('pending', 'processing', 'completed', 'failed')",
            name="ck_document_processing_records_processing_status",
        ),
        Index(
            "ix_document_processing_records_document_id",
            "document_id",
        ),
        Index(
            "ix_document_processing_records_processing_status",
            "processing_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credential_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    predicted_category: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    processing_status: Mapped[str] = mapped_column(String(30), nullable=False)
    extracted_text_reference: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[CredentialDocument] = relationship(
        back_populates="processing_records"
    )

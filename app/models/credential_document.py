from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.credential import Credential
    from app.models.document_chunk import DocumentChunk
    from app.models.document_processing_record import DocumentProcessingRecord
    from app.models.user import User


class CredentialDocument(Base):
    __tablename__ = "credential_documents"
    __table_args__ = (
        UniqueConstraint(
            "storage_key", name="uq_credential_documents_storage_key"
        ),
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_credential_documents_file_size_positive",
        ),
        Index(
            "ix_credential_documents_credential_id",
            "credential_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credentials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    credential: Mapped[Credential] = relationship(back_populates="documents")
    uploaded_by_user: Mapped[User] = relationship(
        back_populates="uploaded_credential_documents"
    )
    processing_records: Mapped[list[DocumentProcessingRecord]] = relationship(
        back_populates="document",
        passive_deletes=True,
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        passive_deletes=True,
    )

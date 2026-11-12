from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.credential import Credential
    from app.models.user import User


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('pending', 'in_progress', 'verified', 'rejected', "
            "'requires_manual_review')",
            name="ck_verification_records_status",
        ),
        Index(
            "ix_verification_records_credential_id",
            "credential_id",
        ),
        Index(
            "ix_verification_records_reviewer_user_id",
            "reviewer_user_id",
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
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    credential: Mapped[Credential] = relationship(
        back_populates="verification_records"
    )
    reviewer: Mapped[User | None] = relationship(
        back_populates="reviewed_verification_records"
    )

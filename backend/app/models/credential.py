from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.credential_document import CredentialDocument
    from app.models.therapist_profile import TherapistProfile
    from app.models.verification_record import VerificationRecord


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        CheckConstraint(
            "credential_type IN "
            "('degree', 'license', 'training', 'supervision', 'other')",
            name="ck_credentials_credential_type",
        ),
        CheckConstraint(
            "status IN "
            "('pending', 'under_review', 'verified', 'rejected', 'expired')",
            name="ck_credentials_status",
        ),
        Index("ix_credentials_therapist_id", "therapist_id"),
        Index("ix_credentials_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("therapist_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_type: Mapped[str] = mapped_column(String(50), nullable=False)
    issuing_organization: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    therapist_profile: Mapped[TherapistProfile] = relationship(
        back_populates="credentials"
    )
    documents: Mapped[list[CredentialDocument]] = relationship(
        back_populates="credential",
        passive_deletes=True,
    )
    verification_records: Mapped[list[VerificationRecord]] = relationship(
        back_populates="credential",
        passive_deletes=True,
    )

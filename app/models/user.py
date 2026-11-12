from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog
    from app.models.credential_document import CredentialDocument
    from app.models.therapist_profile import TherapistProfile
    from app.models.verification_record import VerificationRecord


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "role IN ('applicant', 'reviewer', 'admin')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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

    therapist_profile: Mapped[TherapistProfile | None] = relationship(
        back_populates="user",
        uselist=False,
        passive_deletes=True,
    )
    uploaded_credential_documents: Mapped[list[CredentialDocument]] = relationship(
        back_populates="uploaded_by_user",
        passive_deletes=True,
    )
    reviewed_verification_records: Mapped[list[VerificationRecord]] = relationship(
        back_populates="reviewer",
        passive_deletes=True,
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="actor",
        passive_deletes=True,
    )

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        return value.strip().lower()

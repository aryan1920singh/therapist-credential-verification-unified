from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql.sqltypes import DateTime

from app.db.base import Base
from app.models import (
    AuditLog,
    Credential,
    CredentialDocument,
    DocumentChunk,
    DocumentProcessingRecord,
    TherapistProfile,
    User,
    VerificationRecord,
)

MODELS = [
    User,
    TherapistProfile,
    Credential,
    CredentialDocument,
    VerificationRecord,
    AuditLog,
    DocumentProcessingRecord,
    DocumentChunk,
]

EXPECTED_TABLES = {
    "users",
    "therapist_profiles",
    "credentials",
    "credential_documents",
    "verification_records",
    "audit_logs",
    "document_processing_records",
    "document_chunks",
}

EXPECTED_FOREIGN_KEYS = {
    "users": set(),
    "therapist_profiles": {"users.id"},
    "credentials": {"therapist_profiles.id"},
    "credential_documents": {"credentials.id", "users.id"},
    "verification_records": {"credentials.id", "users.id"},
    "audit_logs": {"users.id"},
    "document_processing_records": {"credential_documents.id"},
    "document_chunks": {"credential_documents.id"},
}


def _check_constraints(table) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _unique_constraints(table) -> dict[str, set[str]]:
    return {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes}


def test_all_models_import_and_register() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(MODELS) == 8
    assert all(model.__table__.name in EXPECTED_TABLES for model in MODELS)


def test_primary_keys_are_uuid_values() -> None:
    for model in MODELS:
        primary_key = list(model.__table__.primary_key.columns)
        assert len(primary_key) == 1
        assert isinstance(primary_key[0].type, UUID)
        assert primary_key[0].type.as_uuid is True
        assert callable(primary_key[0].default.arg)
        assert isinstance(primary_key[0].default.arg(None), uuid.UUID)


def test_foreign_keys_exist() -> None:
    for model in MODELS:
        actual = {
            foreign_key.target_fullname
            for foreign_key in model.__table__.foreign_keys
        }
        assert actual == EXPECTED_FOREIGN_KEYS[model.__tablename__]


def test_foreign_key_delete_behavior_protects_history() -> None:
    verification_targets = {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in VerificationRecord.__table__.foreign_keys
    }
    audit_targets = {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in AuditLog.__table__.foreign_keys
    }
    document_targets = {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in CredentialDocument.__table__.foreign_keys
    }
    processing_targets = {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in DocumentProcessingRecord.__table__.foreign_keys
    }
    chunk_targets = {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in DocumentChunk.__table__.foreign_keys
    }

    assert verification_targets["users.id"] == "SET NULL"
    assert verification_targets["credentials.id"] == "RESTRICT"
    assert audit_targets["users.id"] == "SET NULL"
    assert document_targets["credentials.id"] == "RESTRICT"
    assert document_targets["users.id"] == "RESTRICT"
    assert processing_targets["credential_documents.id"] == "RESTRICT"
    assert chunk_targets["credential_documents.id"] == "RESTRICT"


def test_unique_email_constraint_exists() -> None:
    uniques = _unique_constraints(User.__table__)
    assert uniques["uq_users_email"] == {"email"}


def test_user_email_is_normalized_to_lowercase() -> None:
    user = User(full_name="Test User", email="  MixedCase@Example.COM  ", role="applicant")
    assert user.email == "mixedcase@example.com"


def test_user_password_hash_is_nullable() -> None:
    assert User.__table__.c.password_hash.nullable is True
    assert User.__table__.c.password_hash.type.length == 255


def test_user_role_check_constraint_exists() -> None:
    checks = _check_constraints(User.__table__)
    assert "ck_users_role" in checks
    for role in ("applicant", "reviewer", "admin"):
        assert role in checks["ck_users_role"]


def test_therapist_profile_is_one_to_one_with_user() -> None:
    assert User.__mapper__.relationships["therapist_profile"].uselist is False
    assert TherapistProfile.__mapper__.relationships["user"].uselist is False

    uniques = _unique_constraints(TherapistProfile.__table__)
    assert uniques["uq_therapist_profiles_user_id"] == {"user_id"}


def test_credential_type_and_status_constraints_exist() -> None:
    checks = _check_constraints(Credential.__table__)

    assert "ck_credentials_credential_type" in checks
    for credential_type in (
        "degree",
        "license",
        "training",
        "supervision",
        "other",
    ):
        assert credential_type in checks["ck_credentials_credential_type"]

    assert "ck_credentials_status" in checks
    for status in (
        "pending",
        "under_review",
        "verified",
        "rejected",
        "expired",
    ):
        assert status in checks["ck_credentials_status"]


def test_credential_status_defaults_to_pending() -> None:
    assert Credential.__table__.c.status.default.arg == "pending"
    assert Credential.__table__.c.expiration_date.nullable is True


def test_credential_indexes_exist() -> None:
    indexes = _index_names(Credential.__table__)
    assert "ix_credentials_therapist_id" in indexes
    assert "ix_credentials_status" in indexes


def test_credential_document_constraints_exist() -> None:
    checks = _check_constraints(CredentialDocument.__table__)
    uniques = _unique_constraints(CredentialDocument.__table__)
    indexes = _index_names(CredentialDocument.__table__)

    assert checks["ck_credential_documents_file_size_positive"] == (
        "file_size_bytes > 0"
    )
    assert uniques["uq_credential_documents_storage_key"] == {"storage_key"}
    assert "ix_credential_documents_credential_id" in indexes
    assert isinstance(
        CredentialDocument.__table__.c.file_size_bytes.type, BigInteger
    )
    assert CredentialDocument.__table__.c.sha256_checksum.type.length == 64


def test_verification_records_preserve_review_history() -> None:
    assert Credential.__mapper__.relationships["verification_records"].uselist is True
    assert VerificationRecord.__mapper__.relationships["reviewer"].uselist is False

    unique_column_sets = _unique_constraints(VerificationRecord.__table__)
    assert all("credential_id" not in columns for columns in unique_column_sets.values())

    checks = _check_constraints(VerificationRecord.__table__)
    assert "ck_verification_records_status" in checks
    for status in (
        "pending",
        "in_progress",
        "verified",
        "rejected",
        "requires_manual_review",
    ):
        assert status in checks["ck_verification_records_status"]

    indexes = _index_names(VerificationRecord.__table__)
    assert "ix_verification_records_credential_id" in indexes
    assert "ix_verification_records_reviewer_user_id" in indexes


def test_audit_log_metadata_is_configured() -> None:
    assert isinstance(AuditLog.__table__.c.details.type, JSONB)
    assert AuditLog.__table__.c.details.nullable is True
    assert AuditLog.__table__.c.actor_user_id.nullable is True

    indexes = _index_names(AuditLog.__table__)
    assert "ix_audit_logs_actor_user_id" in indexes
    assert "ix_audit_logs_created_at" in indexes


def test_all_timestamp_columns_are_timezone_aware() -> None:
    for model in MODELS:
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True


def test_relationship_names_and_cardinality_are_configured() -> None:
    expected_relationships = {
        User: {
            "therapist_profile": False,
            "uploaded_credential_documents": True,
            "reviewed_verification_records": True,
            "audit_logs": True,
        },
        TherapistProfile: {
            "user": False,
            "credentials": True,
        },
        Credential: {
            "therapist_profile": False,
            "documents": True,
            "verification_records": True,
        },
        CredentialDocument: {
            "credential": False,
            "uploaded_by_user": False,
            "processing_records": True,
            "chunks": True,
        },
        VerificationRecord: {
            "credential": False,
            "reviewer": False,
        },
        AuditLog: {
            "actor": False,
        },
        DocumentProcessingRecord: {
            "document": False,
        },
        DocumentChunk: {
            "document": False,
        },
    }

    for model, relationships in expected_relationships.items():
        for name, is_list in relationships.items():
            assert model.__mapper__.relationships[name].uselist is is_list

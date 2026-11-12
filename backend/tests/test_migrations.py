from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from app.db.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_TABLES = {
    "users",
    "therapist_profiles",
    "credentials",
    "credential_documents",
    "verification_records",
    "audit_logs",
}
NEW_TABLES = {
    "document_processing_records",
    "document_chunks",
}
EXPECTED_TABLES = CORE_TABLES | NEW_TABLES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


@pytest.fixture(scope="module")
def database_engine():
    database_engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield database_engine
    database_engine.dispose()


def test_configured_database_is_the_development_database(database_engine) -> None:
    with database_engine.connect() as connection:
        database_name = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

    assert database_name == "therapist_credentials"


def test_alembic_has_a_single_head_and_initial_revision(
    alembic_config: Config,
) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()
    bases = script.get_bases()

    assert len(heads) == 1
    assert len(bases) == 1
    assert script.get_revision(heads[0]) is not None
    initial_revision = script.get_revision(bases[0])
    assert initial_revision is not None
    assert initial_revision.down_revision is None


def test_database_revision_matches_alembic_head(
    alembic_config: Config, database_engine
) -> None:
    head = ScriptDirectory.from_config(alembic_config).get_current_head()

    with database_engine.connect() as connection:
        current_revision = MigrationContext.configure(
            connection
        ).get_current_revision()

    assert current_revision == head


def test_model_metadata_matches_database_schema(database_engine) -> None:
    with database_engine.connect() as connection:
        migration_context = MigrationContext.configure(
            connection,
            opts={"compare_type": True},
        )
        differences = compare_metadata(migration_context, Base.metadata)

    assert differences == []


def test_expected_tables_exist_after_migration(database_engine) -> None:
    tables = set(inspect(database_engine).get_table_names(schema="public"))

    assert tables == EXPECTED_TABLES | {"alembic_version"}


def test_initial_migration_creates_core_tables(alembic_config: Config) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    revision = list(script.walk_revisions())[-1]

    migration_source = Path(revision.path).read_text(encoding="utf-8")
    for table in CORE_TABLES:
        assert f"op.create_table('{table}'" in migration_source


def test_new_migration_creates_document_tables(alembic_config: Config) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision(script.get_current_head())
    assert revision is not None

    migration_source = Path(revision.path).read_text(encoding="utf-8")
    for table in NEW_TABLES:
        assert f"op.create_table('{table}'" in migration_source


def test_migrations_have_reversible_upgrade_and_downgrade(
    alembic_config: Config,
) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    revisions = list(script.walk_revisions())
    assert len(revisions) == 2

    for revision in revisions:
        assert callable(revision.module.upgrade)
        assert callable(revision.module.downgrade)


def test_database_constraints_exist_after_migration(database_engine) -> None:
    inspector = inspect(database_engine)

    user_checks = {
        check["name"]
        for check in inspector.get_check_constraints("users")
    }
    credential_checks = {
        check["name"]
        for check in inspector.get_check_constraints("credentials")
    }
    document_checks = {
        check["name"]
        for check in inspector.get_check_constraints("credential_documents")
    }
    verification_checks = {
        check["name"]
        for check in inspector.get_check_constraints("verification_records")
    }
    processing_checks = {
        check["name"]
        for check in inspector.get_check_constraints(
            "document_processing_records"
        )
    }
    chunk_checks = {
        check["name"]
        for check in inspector.get_check_constraints("document_chunks")
    }

    assert "ck_users_role" in user_checks
    assert {
        "ck_credentials_credential_type",
        "ck_credentials_status",
    } <= credential_checks
    assert "ck_credential_documents_file_size_positive" in document_checks
    assert "ck_verification_records_status" in verification_checks
    assert {
        "ck_document_processing_records_predicted_category",
        "ck_document_processing_records_confidence_score_range",
        "ck_document_processing_records_processing_status",
    } <= processing_checks
    assert {
        "ck_document_chunks_chunk_index_non_negative",
        "ck_document_chunks_page_number_positive",
        "ck_document_chunks_embedding_requires_model",
    } <= chunk_checks

    user_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("users")
    }
    profile_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("therapist_profiles")
    }
    chunk_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("document_chunks")
    }

    assert "uq_users_email" in user_uniques
    assert "uq_therapist_profiles_user_id" in profile_uniques
    assert "uq_document_chunks_document_id_chunk_index" in chunk_uniques

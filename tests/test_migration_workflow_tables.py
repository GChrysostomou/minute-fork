"""Integration test for migration 7a27cd205676 (e.g. adding workflow tables)."""

import uuid
from collections.abc import Generator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

import common.database.postgres_database as pgdb
from alembic import command
from alembic.config import Config
from common.settings import get_settings

PRE_REVISION = "c4f9d2a1b8e3"
TARGET_REVISION = "7a27cd205676"

settings = get_settings()


def _url(db_name: str) -> str:
    """Build a psycopg2 connection URL for the given database name."""
    return (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{db_name}"
    )


def _table_exists(conn: sa.Connection, table_name: str) -> bool:
    """Return True if a table with the given name exists in the public schema."""
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables " "WHERE table_schema = 'public' AND table_name = :t"),
        {"t": table_name},
    ).first()
    return result is not None


def _fk_exists(conn: sa.Connection, table_name: str, column_name: str, foreign_table: str) -> bool:
    """Return True if a FK constraint exists from table_name.column_name to foreign_table."""
    result = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = rc.constraint_name
              AND kcu.constraint_schema = rc.constraint_schema
            JOIN information_schema.key_column_usage kcu2
              ON kcu2.constraint_name = rc.unique_constraint_name
              AND kcu2.constraint_schema = rc.unique_constraint_schema
              AND kcu2.ordinal_position = kcu.position_in_unique_constraint
            WHERE kcu.table_name = :table
              AND kcu.column_name = :col
              AND kcu2.table_name = :ftable
            """
        ),
        {"table": table_name, "col": column_name, "ftable": foreign_table},
    ).first()
    return result is not None


def _index_exists(conn: sa.Connection, index_name: str) -> bool:
    """Return True if an index with the given name exists in the public schema."""
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :i"),
        {"i": index_name},
    ).first()
    return result is not None


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[sa.Engine, Config], None, None]:
    """Create a throwaway DB at PRE_REVISION; yield (engine, alembic Config); drop after.

    Skips automatically if Postgres is not reachable, so the suite still passes
    in environments without a running database (e.g. offline CI lint jobs).
    """
    maintenance_engine = sa.create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with maintenance_engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except OperationalError:
        pytest.skip("Postgres is not reachable; this test requires docker-compose Postgres running.")

    test_db_name = f"minute_mig_test_{uuid.uuid4().hex[:10]}"
    with maintenance_engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{test_db_name}"'))

    # Point both the project's engine and alembic env at the throwaway DB.
    test_engine = sa.create_engine(_url(test_db_name))
    monkeypatch.setattr(pgdb, "engine", test_engine)

    cfg = Config("alembic.ini")
    command.upgrade(cfg, PRE_REVISION)

    try:
        yield test_engine, cfg
    finally:
        test_engine.dispose()
        with maintenance_engine.connect() as conn:
            # Kill any lingering connections before dropping.
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": test_db_name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        maintenance_engine.dispose()


class TestWorkflowTablesUpgrade:
    """Verify that applying migration 7a27cd205676 creates all expected schema objects.

    Each test upgrades from PRE_REVISION to TARGET_REVISION in an isolated
    throwaway database and then inspects the resulting schema.
    """

    def test_all_three_tables_exist(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """All three workflow tables must be present after the upgrade."""
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)

        with engine.connect() as conn:
            assert _table_exists(conn, "workflow_run"), "workflow_run table should exist"
            assert _table_exists(conn, "workflow_action"), "workflow_action table should exist"
            assert _table_exists(conn, "workflow_credential"), "workflow_credential table should exist"

    def test_foreign_key_constraints_are_in_place(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """Every FK relationship declared in the models must exist in the database.

        Covers:
        - workflow_run.transcription_id  -> transcription
        - workflow_run.user_id           -> user
        - workflow_action.workflow_run_id -> workflow_run
        - workflow_credential.user_id    -> user
        """
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)

        with engine.connect() as conn:
            assert _fk_exists(
                conn, "workflow_run", "transcription_id", "transcription"
            ), "workflow_run.transcription_id should FK to transcription"
            assert _fk_exists(conn, "workflow_run", "user_id", "user"), "workflow_run.user_id should FK to user"
            assert _fk_exists(
                conn, "workflow_action", "workflow_run_id", "workflow_run"
            ), "workflow_action.workflow_run_id should FK to workflow_run"
            assert _fk_exists(
                conn, "workflow_credential", "user_id", "user"
            ), "workflow_credential.user_id should FK to user"

    def test_indexes_are_in_place(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """All three indexes declared in the migration must exist after upgrade.

        Covers:
        - ix_workflow_run_transcription_id  (non-unique, for FK lookup performance)
        - ix_workflow_run_user_id           (non-unique, for FK lookup performance)
        - ix_workflow_credential_user_service (unique, enforces one credential per user per service)
        """
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)

        with engine.connect() as conn:
            assert _index_exists(
                conn, "ix_workflow_run_transcription_id"
            ), "index ix_workflow_run_transcription_id should exist"
            assert _index_exists(conn, "ix_workflow_run_user_id"), "index ix_workflow_run_user_id should exist"
            assert _index_exists(
                conn, "ix_workflow_credential_user_service"
            ), "index ix_workflow_credential_user_service should exist"

    def test_credential_unique_index_is_enforced(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """Inserting a second credential for the same user and service must be rejected.

        The unique index on (user_id, service_name) ensures that each user has at
        most one stored credential per external service at any given time.
        """
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)

        user_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    'INSERT INTO "user" (id, email, created_datetime, updated_datetime) '
                    "VALUES (:i, :e, NOW(), NOW())"
                ),
                {"i": user_id, "e": "test@example.com"},
            )
            # First credential for this user+service is allowed.
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_credential "
                    "(id, user_id, service_name, encrypted_token, created_datetime, updated_datetime) "
                    "VALUES (gen_random_uuid(), :u, :s, :t, NOW(), NOW())"
                ),
                {"u": user_id, "s": "github", "t": "enc_token_1"},
            )

        # A second credential for the same user+service must be rejected.
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_credential "
                    "(id, user_id, service_name, encrypted_token, created_datetime, updated_datetime) "
                    "VALUES (gen_random_uuid(), :u, :s, :t, NOW(), NOW())"
                ),
                {"u": user_id, "s": "github", "t": "enc_token_2"},
            )

    def test_cascade_delete_workflow_run_removes_actions(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """Deleting a WorkflowRun must cascade-delete all of its WorkflowActions.

        This validates the ON DELETE CASCADE constraint on
        workflow_action.workflow_run_id and ensures no orphaned action rows are
        left behind when a run is removed.
        """
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)

        user_id = uuid.uuid4()
        transcription_id = uuid.uuid4()
        run_id = uuid.uuid4()
        action_id = uuid.uuid4()

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    'INSERT INTO "user" (id, email, created_datetime, updated_datetime) '
                    "VALUES (:i, :e, NOW(), NOW())"
                ),
                {"i": user_id, "e": "cascade@example.com"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO transcription (id, user_id, created_datetime, updated_datetime) "
                    "VALUES (:i, :u, NOW(), NOW())"
                ),
                {"i": transcription_id, "u": user_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_run "
                    "(id, transcription_id, user_id, workflow_name, created_datetime, updated_datetime) "
                    "VALUES (:i, :t, :u, 'test_workflow', NOW(), NOW())"
                ),
                {"i": run_id, "t": transcription_id, "u": user_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_action "
                    "(id, workflow_run_id, position, action_type, description, created_datetime, updated_datetime) "
                    "VALUES (:i, :r, 1, 'create_ticket', 'Test action', NOW(), NOW())"
                ),
                {"i": action_id, "r": run_id},
            )

        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM workflow_run WHERE id = :i"), {"i": run_id})

        with engine.connect() as conn:
            remaining: int = conn.execute(
                sa.text("SELECT COUNT(*) FROM workflow_action WHERE id = :i"), {"i": action_id}
            ).scalar_one()
            assert remaining == 0, "workflow_action should be deleted when parent workflow_run is deleted"


class TestWorkflowTablesDowngrade:
    """Verify that rolling back migration 7a27cd205676 removes all schema objects it created."""

    def test_all_three_tables_are_dropped(self, migration_db: tuple[sa.Engine, Config]) -> None:
        """All three workflow tables and their indexes must be absent after downgrade."""
        engine, cfg = migration_db
        command.upgrade(cfg, TARGET_REVISION)
        command.downgrade(cfg, PRE_REVISION)

        with engine.connect() as conn:
            assert not _table_exists(conn, "workflow_run"), "workflow_run should be dropped on downgrade"
            assert not _table_exists(conn, "workflow_action"), "workflow_action should be dropped on downgrade"
            assert not _table_exists(conn, "workflow_credential"), "workflow_credential should be dropped on downgrade"

            assert not _index_exists(conn, "ix_workflow_run_transcription_id")
            assert not _index_exists(conn, "ix_workflow_run_user_id")
            assert not _index_exists(conn, "ix_workflow_credential_user_service")

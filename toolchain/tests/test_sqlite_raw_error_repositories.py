from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

import pytest

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.db import CURRENT_SCHEMA_VERSION, connect
from esp_mcp_toolchain.database.migrations import DatabaseMigrationError, init_database


PROJECT_ID = "sqlite-v3-project"
RUN_ID = "sqlite-v3-run"
CREATED_AT = "2026-07-27T15:00:00+00:00"


def _load_repository(name: str):
    return importlib.import_module(f"esp_mcp_toolchain.database.{name}")


def _create_run(database: Path, *, project_id: str = PROJECT_ID, run_id: str = RUN_ID) -> None:
    log_repository.create_run(
        database,
        project_id=project_id,
        run_id=run_id,
        task_type="sqlite_v3_test",
        started_at=CREATED_AT,
    )


def _create_v2_database(
    database: Path,
    *,
    raw_sha256: str | None = "a" * 64,
) -> dict[str, str]:
    event_uuid = str(uuid4())
    raw_log_id = str(uuid4())
    error_id = str(uuid4())
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );

            CREATE TABLE runs (
              project_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              task_type TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              ended_at TEXT,
              next_sequence_no INTEGER NOT NULL DEFAULT 1,
              selected_port TEXT,
              summary TEXT,
              payload_json TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY(project_id, run_id)
            );

            CREATE TABLE events (
              event_uuid TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              sequence_no INTEGER NOT NULL,
              ts TEXT NOT NULL,
              phase TEXT NOT NULL,
              level TEXT NOT NULL,
              tool TEXT NOT NULL,
              source TEXT NOT NULL,
              message TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(project_id, run_id, sequence_no),
              FOREIGN KEY(project_id, run_id)
                REFERENCES runs(project_id, run_id)
                ON DELETE CASCADE
            );

            CREATE TABLE legacy_jsonl_imports (
              project_id TEXT NOT NULL,
              source_path TEXT NOT NULL,
              content_sha256 TEXT NOT NULL,
              event_count INTEGER NOT NULL,
              imported_at TEXT NOT NULL,
              PRIMARY KEY(project_id, source_path, content_sha256)
            );

            CREATE TABLE raw_logs (
              project_id TEXT NOT NULL,
              raw_log_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              sha256 TEXT,
              PRIMARY KEY(project_id, raw_log_id),
              FOREIGN KEY(project_id, run_id)
                REFERENCES runs(project_id, run_id)
                ON DELETE CASCADE
            );

            CREATE TABLE errors (
              project_id TEXT NOT NULL,
              error_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              error_kind TEXT NOT NULL,
              file TEXT,
              line INTEGER,
              column INTEGER,
              exception_type TEXT,
              message TEXT,
              raw_text TEXT,
              recoverable INTEGER,
              created_at TEXT NOT NULL,
              PRIMARY KEY(project_id, error_id),
              FOREIGN KEY(project_id, run_id)
                REFERENCES runs(project_id, run_id)
                ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO runs (
              project_id, run_id, task_type, status, started_at,
              next_sequence_no, payload_json
            ) VALUES (?, ?, 'legacy_v2', 'succeeded', ?, 2, '{}')
            """,
            (PROJECT_ID, RUN_ID, CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO events (
              event_uuid, project_id, run_id, sequence_no, ts, phase,
              level, tool, source, message, payload_json
            ) VALUES (?, ?, ?, 1, ?, 'complete', 'info', 'legacy_v2',
                      'pytest', 'preserved event', '{"legacy":true}')
            """,
            (event_uuid, PROJECT_ID, RUN_ID, CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO legacy_jsonl_imports (
              project_id, source_path, content_sha256, event_count, imported_at
            ) VALUES (?, 'sessions/legacy.jsonl', ?, 1, ?)
            """,
            (PROJECT_ID, "b" * 64, CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO raw_logs (
              project_id, raw_log_id, run_id, kind, path, created_at, sha256
            ) VALUES (?, ?, ?, 'serial_capture', 'sessions/legacy.raw', ?, ?)
            """,
            (PROJECT_ID, raw_log_id, RUN_ID, CREATED_AT, raw_sha256),
        )
        connection.execute(
            """
            INSERT INTO errors (
              project_id, error_id, run_id, error_kind, file, line, column,
              exception_type, message, raw_text, recoverable, created_at
            ) VALUES (?, ?, ?, 'micropython_exception', 'main.py', 7, 3,
                      'ValueError', 'legacy error', 'Traceback...', 1, ?)
            """,
            (PROJECT_ID, error_id, RUN_ID, CREATED_AT),
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (2, 'formal_log_database', ?)",
            (CREATED_AT,),
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()
    return {
        "event_uuid": event_uuid,
        "raw_log_id": raw_log_id,
        "error_id": error_id,
    }


def _assert_v3_raw_error_contract(
    connection: sqlite3.Connection,
    *,
    project_id: str = PROJECT_ID,
    run_id: str = RUN_ID,
) -> None:
    raw_index_columns = {
        row["name"]: [
            item["name"]
            for item in connection.execute(f'PRAGMA index_info("{row["name"]}")')
        ]
        for row in connection.execute("PRAGMA index_list(raw_logs)")
    }
    error_index_columns = {
        row["name"]: [
            item["name"]
            for item in connection.execute(f'PRAGMA index_info("{row["name"]}")')
        ]
        for row in connection.execute("PRAGMA index_list(errors)")
    }
    assert raw_index_columns["idx_raw_logs_project_run_created"] == [
        "project_id",
        "run_id",
        "created_at",
        "raw_log_id",
    ]
    assert raw_index_columns["idx_raw_logs_project_kind_created"] == [
        "project_id",
        "kind",
        "created_at",
        "raw_log_id",
    ]
    assert error_index_columns["idx_errors_project_run_created"] == [
        "project_id",
        "run_id",
        "created_at",
        "error_id",
    ]
    assert error_index_columns["idx_errors_project_kind_created"] == [
        "project_id",
        "error_kind",
        "created_at",
        "error_id",
    ]
    assert {
        (row["from"], row["to"], row["on_delete"])
        for row in connection.execute("PRAGMA foreign_key_list(raw_logs)")
    } == {
        ("project_id", "project_id", "CASCADE"),
        ("run_id", "run_id", "CASCADE"),
    }
    assert {
        (row["from"], row["to"], row["on_delete"])
        for row in connection.execute("PRAGMA foreign_key_list(errors)")
    } == {
        ("project_id", "project_id", "CASCADE"),
        ("run_id", "run_id", "CASCADE"),
    }

    invalid_statements = [
        (
            """
            INSERT INTO raw_logs
              (project_id, raw_log_id, run_id, kind, path, created_at, sha256)
            VALUES (?, ?, ?, 'capture', '../escape.bin', ?, ?)
            """,
            (project_id, str(uuid4()), run_id, CREATED_AT, "a" * 64),
        ),
        (
            """
            INSERT INTO raw_logs
              (project_id, raw_log_id, run_id, kind, path, created_at, sha256)
            VALUES (?, ?, ?, 'capture', 'sessions/capture.bin', ?, 'bad')
            """,
            (project_id, str(uuid4()), run_id, CREATED_AT),
        ),
        (
            """
            INSERT INTO errors
              (project_id, error_id, run_id, error_kind, line, recoverable, created_at)
            VALUES (?, ?, ?, 'runtime', 0, 1, ?)
            """,
            (project_id, str(uuid4()), run_id, CREATED_AT),
        ),
        (
            """
            INSERT INTO errors
              (project_id, error_id, run_id, error_kind, line, recoverable, created_at)
            VALUES (?, ?, ?, 'runtime', 'not-an-integer', 1, ?)
            """,
            (project_id, str(uuid4()), run_id, CREATED_AT),
        ),
        (
            """
            INSERT INTO errors
              (project_id, error_id, run_id, error_kind, recoverable, created_at)
            VALUES (?, ?, ?, 'runtime', 2, ?)
            """,
            (project_id, str(uuid4()), run_id, CREATED_AT),
        ),
    ]
    for statement, parameters in invalid_statements:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, parameters)


def test_schema_v3_has_raw_error_constraints_indexes_and_foreign_keys(tmp_path):
    database = tmp_path / "schema-v3.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)

    connection = connect(database)
    try:
        assert CURRENT_SCHEMA_VERSION == 3
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        _assert_v3_raw_error_contract(connection)
    finally:
        connection.close()


def test_v2_to_v3_migration_preserves_all_rows_and_markers(tmp_path):
    database = tmp_path / "v2.sqlite"
    identifiers = _create_v2_database(database)

    init_database(database, project_id=PROJECT_ID)

    connection = connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_jsonl_imports"
        ).fetchone()[0] == 1
        raw = connection.execute("SELECT * FROM raw_logs").fetchone()
        error = connection.execute("SELECT * FROM errors").fetchone()
        assert raw["raw_log_id"] == identifiers["raw_log_id"]
        assert raw["path"] == "sessions/legacy.raw"
        assert raw["sha256"] == "a" * 64
        assert error["error_id"] == identifiers["error_id"]
        assert error["line"] == 7
        assert error["recoverable"] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        } == {2, 3}
        assert not {
            "raw_logs_legacy_v2",
            "errors_legacy_v2",
        } & {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        _assert_v3_raw_error_contract(connection)
    finally:
        connection.close()


def test_v2_to_v3_migration_is_repeatable_and_concurrent_safe(tmp_path):
    database = tmp_path / "repeatable-v2.sqlite"
    _create_v2_database(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        initialized = list(
            executor.map(
                lambda _index: init_database(database, project_id=PROJECT_ID),
                range(2),
            )
        )
    init_database(database, project_id=PROJECT_ID)

    assert initialized == [database, database]
    connection = connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 1
        _assert_v3_raw_error_contract(connection)
    finally:
        connection.close()


def test_misstamped_weak_v3_is_rejected_instead_of_reaffirmed(tmp_path):
    database = tmp_path / "misstamped-v3.sqlite"
    _create_v2_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 0
        assert {
            row[1] for row in connection.execute("PRAGMA index_list(raw_logs)")
        }.isdisjoint(
            {
                "idx_raw_logs_project_run_created",
                "idx_raw_logs_project_kind_created",
            }
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "table_contract",
    [
        "PRIMARY KEY(project_id, raw_log_id)",
        """
        PRIMARY KEY(raw_log_id),
        FOREIGN KEY(project_id, run_id)
          REFERENCES runs(project_id, run_id)
          ON DELETE CASCADE
        """,
    ],
    ids=("missing-foreign-key", "wrong-primary-key"),
)
def test_misstamped_v3_rejects_wrong_raw_log_keys(tmp_path, table_contract):
    database = tmp_path / "misstamped-v3-keys.sqlite"
    _create_v2_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE raw_logs RENAME TO raw_logs_old")
        connection.executescript(
            f"""
            CREATE TABLE raw_logs (
              project_id TEXT NOT NULL,
              raw_log_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              sha256 TEXT,
              {table_contract}
            );
            INSERT INTO raw_logs
              (project_id, raw_log_id, run_id, kind, path, created_at, sha256)
            SELECT project_id, raw_log_id, run_id, kind, path, created_at, sha256
            FROM raw_logs_old;
            DROP TABLE raw_logs_old;
            PRAGMA user_version = 3;
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 1
    finally:
        connection.close()


def test_misstamped_v3_rejects_same_named_index_with_wrong_columns(tmp_path):
    database = tmp_path / "misstamped-v3-index.sqlite"
    _create_v2_database(database)
    init_database(database, project_id=PROJECT_ID)
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP INDEX idx_raw_logs_project_run_created")
        connection.execute(
            """
            CREATE INDEX idx_raw_logs_project_run_created
            ON raw_logs(project_id, path)
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert [
            row[2]
            for row in connection.execute(
                'PRAGMA index_info("idx_raw_logs_project_run_created")'
            )
        ] == ["project_id", "path"]
    finally:
        connection.close()


def test_malformed_v2_stamp_is_rejected_without_reinterpreting_it_as_v1(tmp_path):
    database = tmp_path / "malformed-v2.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT value FROM unrelated").fetchone()[0] == (
            "preserve me"
        )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        } == {"unrelated"}
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("raw_logs", "sha256"),
        ("errors", "file"),
    ],
)
def test_v2_missing_copy_column_rolls_back_without_fabricating_data(
    tmp_path,
    table,
    column,
):
    database = tmp_path / f"missing-{table}-{column}.sqlite"
    _create_v2_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert column not in {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 0
        assert f"{table}_legacy_v2" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


@pytest.mark.parametrize("table", ["raw_logs", "errors"])
def test_v2_extra_column_rolls_back_without_discarding_extension_data(
    tmp_path,
    table,
):
    database = tmp_path / f"extra-{table}.sqlite"
    _create_v2_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            f'ALTER TABLE "{table}" ADD COLUMN source_event_uuid TEXT'
        )
        connection.execute(
            f'UPDATE "{table}" SET source_event_uuid = ?',
            ("event-evidence-123",),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            f'SELECT source_event_uuid FROM "{table}"'
        ).fetchone()[0] == "event-evidence-123"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 0
        assert f"{table}_legacy_v2" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


def test_v2_to_v3_constraint_failure_rolls_back_without_stamping_version(tmp_path):
    database = tmp_path / "invalid-v2.sqlite"
    _create_v2_database(database, raw_sha256="not-a-sha256")

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT sha256 FROM raw_logs").fetchone()[0] == "not-a-sha256"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "raw_logs" in tables
        assert "errors" in tables
        assert "raw_logs_legacy_v2" not in tables
        assert "errors_legacy_v2" not in tables
    finally:
        connection.close()


def test_v2_to_v3_foreign_key_failure_rolls_back_after_copy(tmp_path):
    database = tmp_path / "orphan-v2.sqlite"
    _create_v2_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "UPDATE raw_logs SET run_id = 'missing-run' WHERE project_id = ?",
            (PROJECT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseMigrationError):
        init_database(database, project_id=PROJECT_ID)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT run_id FROM raw_logs").fetchone()[0] == "missing-run"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "raw_logs" in tables
        assert "errors" in tables
        assert "raw_logs_legacy_v2" not in tables
        assert "errors_legacy_v2" not in tables
    finally:
        connection.close()


def test_v1_raw_and_error_rows_migrate_directly_to_v3(tmp_path):
    database = tmp_path / "v1-raw-error.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE raw_logs (
              raw_log_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              sha256 TEXT
            );
            CREATE TABLE errors (
              error_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              error_kind TEXT NOT NULL,
              file TEXT,
              line INTEGER,
              column INTEGER,
              exception_type TEXT,
              message TEXT,
              raw_text TEXT,
              recoverable INTEGER,
              created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO raw_logs
              (raw_log_id, run_id, kind, path, created_at, sha256)
            VALUES ('legacy-raw-id', 'legacy-run', 'capture',
                    'sessions/legacy.raw', ?, ?)
            """,
            (CREATED_AT, "f" * 64),
        )
        connection.execute(
            """
            INSERT INTO errors
              (error_id, run_id, error_kind, file, line, column,
               exception_type, message, raw_text, recoverable, created_at)
            VALUES ('legacy-error-id', 'legacy-run', 'runtime', 'main.py',
                    5, 2, 'RuntimeError', 'legacy boom',
                    'RuntimeError: legacy boom', 0, ?)
            """,
            (CREATED_AT,),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()

    init_database(database, project_id=PROJECT_ID)

    connection = connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT raw_log_id FROM raw_logs").fetchone()[0] == (
            "legacy-raw-id"
        )
        assert connection.execute("SELECT error_id FROM errors").fetchone()[0] == (
            "legacy-error-id"
        )
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        _assert_v3_raw_error_contract(
            connection,
            project_id=PROJECT_ID,
            run_id="legacy-run",
        )
    finally:
        connection.close()


def test_raw_log_repository_is_strictly_idempotent_and_queryable(tmp_path):
    raw_repository = _load_repository("raw_log_repository")
    database = tmp_path / "raw.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)
    raw_log_id = raw_repository.stable_raw_log_id(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        kind="serial_capture",
        path="sessions/capture.raw",
    )
    payload = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "raw_log_id": raw_log_id,
        "kind": "serial_capture",
        "path": "sessions/capture.raw",
        "created_at": CREATED_AT,
        "sha256": "c" * 64,
    }

    first, inserted = log_repository.register_raw_log(database, **payload)
    retry, retry_inserted = log_repository.register_raw_log(database, **payload)

    assert inserted is True
    assert retry_inserted is False
    assert retry == first
    assert first["sha256"] == "c" * 64
    assert log_repository.get_raw_log(
        database,
        project_id=PROJECT_ID,
        raw_log_id=raw_log_id,
    ) == first
    assert log_repository.get_run_raw_logs(
        database,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
    ) == [first]

    with pytest.raises(raw_repository.RawLogConflictError):
        log_repository.register_raw_log(
            database,
            **{**payload, "path": "sessions/different.raw"},
        )


def test_error_repository_is_strictly_idempotent_and_queryable(tmp_path):
    error_repository = _load_repository("error_repository")
    database = tmp_path / "errors.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)
    error_id = error_repository.stable_error_id(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        occurrence_key="event-error-1",
        error_kind="micropython_exception",
        file="main.py",
        line=12,
        column=4,
        exception_type="ValueError",
        message="bad value",
        raw_text="Traceback...",
    )
    payload = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "error_id": error_id,
        "error_kind": "micropython_exception",
        "file": "main.py",
        "line": 12,
        "column": 4,
        "exception_type": "ValueError",
        "message": "bad value",
        "raw_text": "Traceback...",
        "recoverable": False,
        "created_at": CREATED_AT,
    }

    first, inserted = log_repository.register_error(database, **payload)
    retry, retry_inserted = log_repository.register_error(database, **payload)

    assert inserted is True
    assert retry_inserted is False
    assert retry == first
    assert first["recoverable"] is False
    assert log_repository.get_error(
        database,
        project_id=PROJECT_ID,
        error_id=error_id,
    ) == first
    assert log_repository.get_run_errors(
        database,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
    ) == [first]

    with pytest.raises(error_repository.ErrorConflictError):
        log_repository.register_error(
            database,
            **{**payload, "message": "different error"},
        )

    second_error_id = error_repository.stable_error_id(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        occurrence_key="event-error-2",
        error_kind="micropython_exception",
        file="main.py",
        line=12,
        column=4,
        exception_type="ValueError",
        message="bad value",
        raw_text="Traceback...",
    )
    second, second_inserted = log_repository.register_error(
        database,
        **{
            **payload,
            "error_id": second_error_id,
            "created_at": "2026-07-27T15:00:01+00:00",
        },
    )
    assert second_inserted is True
    assert second["error_id"] != first["error_id"]
    assert len(
        log_repository.get_run_errors(
            database,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
        )
    ) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_log_id", "not-a-uuid"),
        ("kind", " "),
        ("path", "/absolute/capture.raw"),
        ("path", "../escape.raw"),
        ("path", r"sessions\capture.raw"),
        ("sha256", "xyz"),
        ("created_at", "2026-07-27T15:00:00"),
    ],
)
def test_raw_log_repository_rejects_invalid_fields(tmp_path, field, value):
    raw_repository = _load_repository("raw_log_repository")
    database = tmp_path / f"invalid-raw-{field}.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)
    payload = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "raw_log_id": str(uuid4()),
        "kind": "serial_capture",
        "path": "sessions/capture.raw",
        "created_at": CREATED_AT,
        "sha256": "d" * 64,
    }
    payload[field] = value

    with pytest.raises(raw_repository.InvalidRawLogError):
        log_repository.register_raw_log(database, **payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("error_id", "not-a-uuid"),
        ("error_kind", ""),
        ("line", 0),
        ("column", -1),
        ("recoverable", 2),
        ("created_at", "2026-07-27T15:00:00"),
    ],
)
def test_error_repository_rejects_invalid_fields(tmp_path, field, value):
    error_repository = _load_repository("error_repository")
    database = tmp_path / f"invalid-error-{field}.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)
    payload = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "error_id": str(uuid4()),
        "error_kind": "micropython_exception",
        "file": "main.py",
        "line": 1,
        "column": 2,
        "exception_type": "ValueError",
        "message": "bad value",
        "raw_text": "Traceback...",
        "recoverable": True,
        "created_at": CREATED_AT,
    }
    payload[field] = value

    with pytest.raises(error_repository.InvalidErrorRecordError):
        log_repository.register_error(database, **payload)


def test_raw_and_error_repositories_reject_missing_or_cross_project_run(tmp_path):
    raw_repository = _load_repository("raw_log_repository")
    error_repository = _load_repository("error_repository")
    database = tmp_path / "foreign-run.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)

    with pytest.raises(raw_repository.InvalidRawLogError):
        log_repository.register_raw_log(
            database,
            project_id="other-project",
            run_id=RUN_ID,
            raw_log_id=str(uuid4()),
            kind="serial_capture",
            path="sessions/capture.raw",
            created_at=CREATED_AT,
            sha256=None,
        )
    with pytest.raises(error_repository.InvalidErrorRecordError):
        log_repository.register_error(
            database,
            project_id=PROJECT_ID,
            run_id="missing-run",
            error_id=str(uuid4()),
            error_kind="runtime",
            file=None,
            line=None,
            column=None,
            exception_type=None,
            message="missing run",
            raw_text=None,
            recoverable=None,
            created_at=CREATED_AT,
        )


def test_concurrent_raw_and_error_retries_create_one_row_each(tmp_path):
    raw_repository = _load_repository("raw_log_repository")
    error_repository = _load_repository("error_repository")
    database = tmp_path / "concurrent.sqlite"
    init_database(database, project_id=PROJECT_ID)
    _create_run(database)
    raw_log_id = raw_repository.stable_raw_log_id(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        kind="monitor_chunk",
        path="monitor/chunk-000001.bin",
    )
    error_id = error_repository.stable_error_id(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        occurrence_key="event-concurrent-error",
        error_kind="runtime",
        file="main.py",
        line=3,
        column=None,
        exception_type="RuntimeError",
        message="boom",
        raw_text="RuntimeError: boom",
    )

    def register(_index: int) -> tuple[bool, bool]:
        _raw, raw_inserted = log_repository.register_raw_log(
            database,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
            raw_log_id=raw_log_id,
            kind="monitor_chunk",
            path="monitor/chunk-000001.bin",
            created_at=CREATED_AT,
            sha256="e" * 64,
        )
        _error, error_inserted = log_repository.register_error(
            database,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
            error_id=error_id,
            error_kind="runtime",
            file="main.py",
            line=3,
            column=None,
            exception_type="RuntimeError",
            message="boom",
            raw_text="RuntimeError: boom",
            recoverable=False,
            created_at=CREATED_AT,
        )
        return raw_inserted, error_inserted

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(register, range(8)))

    assert sum(raw for raw, _error in results) == 1
    assert sum(error for _raw, error in results) == 1
    assert len(
        log_repository.get_run_raw_logs(
            database,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
        )
    ) == 1
    assert len(
        log_repository.get_run_errors(
            database,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
        )
    ) == 1
    assert UUID(raw_log_id).version == 5
    assert UUID(error_id).version == 5


def test_stable_repository_ids_are_deterministic_and_identity_sensitive():
    raw_repository = _load_repository("raw_log_repository")
    error_repository = _load_repository("error_repository")
    raw_arguments = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "kind": "serial_capture",
        "path": "sessions/capture.raw",
    }
    error_arguments = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "occurrence_key": "event-stable-error",
        "error_kind": "runtime",
        "file": "main.py",
        "line": 3,
        "column": None,
        "exception_type": "RuntimeError",
        "message": "boom",
        "raw_text": "RuntimeError: boom",
    }

    raw_id = raw_repository.stable_raw_log_id(**raw_arguments)
    error_id = error_repository.stable_error_id(**error_arguments)

    assert raw_repository.stable_raw_log_id(**raw_arguments) == raw_id
    assert error_repository.stable_error_id(**error_arguments) == error_id
    assert raw_repository.stable_raw_log_id(
        **{**raw_arguments, "path": "sessions/other.raw"}
    ) != raw_id
    assert error_repository.stable_error_id(
        **{**error_arguments, "message": "different"}
    ) != error_id
    assert error_repository.stable_error_id(
        **{**error_arguments, "occurrence_key": "event-stable-error-2"}
    ) != error_id

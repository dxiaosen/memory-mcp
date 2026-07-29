"""SQLite health-check and versioned-migration entry point."""

import argparse
import logging
import sqlite3
from contextlib import closing
from os import PathLike
from pathlib import Path

from agent_lab.config import get_memory_settings
from agent_lab.memory.adapters.sqlite.repository import connection_factory
from agent_lab.observability import configure_logging_from_settings, log_event

DEFAULT_DATABASE_PATH = Path(".agent-lab/memory.db")
MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
DatabasePath = str | PathLike[str]
_LOGGER = logging.getLogger(__name__)


def get_database_path() -> Path:
    """Return the configured database path or the project-local default."""

    return get_memory_settings().memory_database_path


def check_health(database_path: DatabasePath) -> None:
    """Verify that the database is initialized and passes ``quick_check``."""

    path = Path(database_path)
    if not path.is_file():
        raise RuntimeError(f"Memory SQLite database does not exist: {path}")

    with closing(connection_factory(path)()) as connection:
        result = tuple(
            row[0] for row in connection.execute("PRAGMA quick_check").fetchall()
        )
        if result != ("ok",):
            raise RuntimeError(f"Memory SQLite quick_check failed: {result}")

        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM memory_schema_migrations"
            ).fetchall()
        }
        expected = {path.name for path in MIGRATIONS_DIRECTORY.glob("*.sql")}
        if not expected.issubset(applied):
            missing = ", ".join(sorted(expected - applied))
            raise RuntimeError(f"Memory SQLite migrations are missing: {missing}")
    log_event(
        _LOGGER,
        logging.INFO,
        "memory.sqlite.health_check.completed",
        database_path=path,
        migration_count=len(expected),
        quick_check="ok",
    )


def apply_migrations(database_path: DatabasePath) -> tuple[str, ...]:
    """Idempotently apply pending SQL files in filename order."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connect = connection_factory(path)
    applied_now: list[str] = []
    log_event(
        _LOGGER,
        logging.INFO,
        "memory.sqlite.migration.started",
        database_path=path,
    )

    with closing(connect()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

        for migration_path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
            already_applied = connection.execute(
                """
                SELECT 1
                FROM memory_schema_migrations
                WHERE version = ?
                """,
                (migration_path.name,),
            ).fetchone()
            if already_applied is not None:
                log_event(
                    _LOGGER,
                    logging.DEBUG,
                    "memory.sqlite.migration.skipped",
                    version=migration_path.name,
                )
                continue

            escaped_version = migration_path.name.replace("'", "''")
            script = migration_path.read_text(encoding="utf-8")
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{script}\n"
                    "INSERT INTO memory_schema_migrations (version) "
                    f"VALUES ('{escaped_version}');\n"
                    "COMMIT;"
                )
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.rollback()
                raise
            applied_now.append(migration_path.name)
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.sqlite.migration.applied",
                version=migration_path.name,
            )

    log_event(
        _LOGGER,
        logging.INFO,
        "memory.sqlite.migration.completed",
        applied_count=len(applied_now),
        database_path=path,
    )
    return tuple(applied_now)


def main() -> None:
    """Provide local ``health`` and ``migrate`` commands."""

    settings = get_memory_settings()
    configure_logging_from_settings(settings)
    parser = argparse.ArgumentParser(description="Manage Memory SQLite")
    parser.add_argument("command", choices=("health", "migrate"))
    parser.add_argument(
        "--database-path",
        type=Path,
        default=settings.memory_database_path,
    )
    args = parser.parse_args()

    if args.command == "health":
        check_health(args.database_path)
        print(f"Memory SQLite is healthy: {args.database_path}")
        return

    applied = apply_migrations(args.database_path)
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Memory SQLite schema is up to date.")


if __name__ == "__main__":
    main()

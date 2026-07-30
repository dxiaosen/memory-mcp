"""PostgreSQL migration and health-check entry point."""

from __future__ import annotations

import argparse
import hashlib
import logging
from dataclasses import dataclass
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

from agent_lab.observability import log_event

_LOGGER = logging.getLogger(__name__)
_MIGRATION_LOCK_NAME = "agent-lab-memory-schema-migrations-v1"


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable PostgreSQL migration bundled with the package."""

    version: str
    sql: str
    checksum: str


def load_migrations() -> tuple[Migration, ...]:
    """Load migrations in filename order and calculate stable checksums."""

    root = files("agent_lab.memory.adapters.postgresql.migrations")
    migrations: list[Migration] = []
    for resource in sorted(root.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".sql"):
            continue
        sql = resource.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=resource.name,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(migrations)


def apply_migrations(database_url: str) -> tuple[str, ...]:
    """Apply pending migrations serially and reject modified history."""

    applied_now: list[str] = []
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.commit()
        connection.execute(
            "SELECT pg_advisory_lock(hashtext(%s))",
            (_MIGRATION_LOCK_NAME,),
        )
        try:
            existing = {
                row["version"]: row["checksum"]
                for row in connection.execute(
                    """
                    SELECT version, checksum
                    FROM memory_schema_migrations
                    ORDER BY version
                    """
                ).fetchall()
            }
            for migration in load_migrations():
                previous_checksum = existing.get(migration.version)
                if previous_checksum is not None:
                    if previous_checksum != migration.checksum:
                        raise RuntimeError(
                            "applied PostgreSQL migration checksum changed: "
                            f"{migration.version}"
                        )
                    continue
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "memory.postgresql.migration.started",
                    version=migration.version,
                )
                try:
                    connection.execute(migration.sql, prepare=False)
                    connection.execute(
                        """
                        INSERT INTO memory_schema_migrations (version, checksum)
                        VALUES (%s, %s)
                        """,
                        (migration.version, migration.checksum),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                applied_now.append(migration.version)
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "memory.postgresql.migration.applied",
                    version=migration.version,
                )
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))",
                (_MIGRATION_LOCK_NAME,),
            )
            connection.commit()
    return tuple(applied_now)


def check_health(database_url: str) -> None:
    """Verify connectivity and the required schema without exposing the DSN."""

    with psycopg.connect(
        database_url,
        connect_timeout=5,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            SELECT
                current_database() AS database_name,
                to_regclass('public.memory_items') IS NOT NULL AS schema_ready
            """
        ).fetchone()
        if row is None or not row["schema_ready"]:
            raise RuntimeError("PostgreSQL is reachable but memory schema is missing")
    log_event(
        _LOGGER,
        logging.DEBUG,
        "memory.postgresql.health_check.completed",
    )


def main() -> None:
    """Run database maintenance commands from the deployment environment."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "health"))
    args = parser.parse_args()

    from agent_lab.memory_mcp.settings import MemoryServerSettings

    settings = MemoryServerSettings.from_environment()
    database_url = settings.require_postgresql_url()
    if args.command == "health":
        check_health(database_url)
        print("Memory PostgreSQL is healthy")
        return
    applied = apply_migrations(database_url)
    if applied:
        print("Applied PostgreSQL migrations: " + ", ".join(applied))
    else:
        print("PostgreSQL schema is up to date")


if __name__ == "__main__":
    main()

"""PostgreSQL migration 加载、执行与 schema 校验。"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

from memory_mcp.logging import log_event

_LOGGER = logging.getLogger(__name__)
_MIGRATION_LOCK_NAME = "memory-mcp-schema-migrations-v1"
_REQUIRED_TABLES = frozenset(
    {
        "memory_schema_migrations",
        "memory_profiles",
        "memory_profile_relations",
        "memory_profile_types",
        "memory_items",
        "memory_revisions",
        "memory_evidence",
        "memory_evidence_documents",
        "memory_capture_runs",
        "memory_review_items",
        "memory_review_item_documents",
        "memory_relations",
        "memory_capture_outcomes",
    }
)
_REQUIRED_EXTENSIONS = frozenset({"pg_trgm"})
_REQUIRED_INDEXES = frozenset(
    {
        "memory_items_recall_subject_trgm_idx",
        "memory_revisions_recall_content_trgm_idx",
        "memory_revisions_maintenance_expiry_idx",
        "memory_review_items_maintenance_idx",
    }
)


@dataclass(frozen=True, slots=True)
class Migration:
    """随包发布的一条不可变 PostgreSQL migration。"""

    version: str
    sql: str
    checksum: str


def load_migrations() -> tuple[Migration, ...]:
    """按文件名顺序加载 migration 并计算稳定 checksum。"""

    root = files("memory_mcp.core.adapters.postgresql.migrations")
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
    """串行执行待处理 migration，并拒绝被修改的历史。"""

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
    """验证连接和必需 schema，且不暴露 DSN。"""

    with psycopg.connect(
        database_url,
        connect_timeout=5,
        row_factory=dict_row,
    ) as connection:
        validate_schema(connection)
    log_event(
        _LOGGER,
        logging.DEBUG,
        "memory.postgresql.health_check.completed",
    )


def validate_schema(connection) -> None:
    """要求当前连接包含全部内置 migration 和核心数据表。"""

    expected = {
        migration.version: migration.checksum for migration in load_migrations()
    }
    with connection.cursor(row_factory=dict_row) as cursor:
        applied = {
            row["version"]: row["checksum"]
            for row in cursor.execute(
                """
                SELECT version, checksum
                FROM memory_schema_migrations
                ORDER BY version
                """
            ).fetchall()
        }
        existing_tables = {
            row["table_name"]
            for row in cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (sorted(_REQUIRED_TABLES),),
            ).fetchall()
        }
        existing_extensions = {
            row["extname"]
            for row in cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = ANY(%s)",
                (sorted(_REQUIRED_EXTENSIONS),),
            ).fetchall()
        }
        existing_indexes = {
            row["indexname"]
            for row in cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = ANY(%s)
                """,
                (sorted(_REQUIRED_INDEXES),),
            ).fetchall()
        }
    missing_migrations = expected.keys() - applied.keys()
    if missing_migrations:
        missing = ", ".join(sorted(missing_migrations))
        raise RuntimeError(f"PostgreSQL migrations are missing: {missing}")
    changed_migrations = tuple(
        version
        for version, checksum in expected.items()
        if applied[version] != checksum
    )
    if changed_migrations:
        changed = ", ".join(sorted(changed_migrations))
        raise RuntimeError(f"PostgreSQL migration checksums changed: {changed}")

    missing_tables = _REQUIRED_TABLES - existing_tables
    if missing_tables:
        missing = ", ".join(sorted(missing_tables))
        raise RuntimeError(f"PostgreSQL memory tables are missing: {missing}")

    missing_extensions = _REQUIRED_EXTENSIONS - existing_extensions
    if missing_extensions:
        missing = ", ".join(sorted(missing_extensions))
        raise RuntimeError(f"PostgreSQL extensions are missing: {missing}")

    missing_indexes = _REQUIRED_INDEXES - existing_indexes
    if missing_indexes:
        missing = ", ".join(sorted(missing_indexes))
        raise RuntimeError(f"PostgreSQL memory indexes are missing: {missing}")

"""Numbered SQL migrations, applied in order, recorded in a ledger.

Two dialect trees live under the repo's ``migrations/`` directory
(``migrations/sqlite/0001_*.sql``, ``migrations/postgres/0001_*.sql``).
The runner applies every file the ledger has not recorded, inside
the caller's connection, and is idempotent on re-run.
"""

from __future__ import annotations

import os
from pathlib import Path


def migrations_dir(dialect: str) -> Path:
    candidates = []
    if os.environ.get("GRID_MIGRATIONS_DIR"):
        candidates.append(Path(os.environ["GRID_MIGRATIONS_DIR"]) / dialect)
    candidates.append(Path(__file__).resolve().parents[2] / "migrations" / dialect)
    candidates.append(Path("/app/migrations") / dialect)
    candidates.append(Path.cwd() / "migrations" / dialect)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def migration_files(dialect: str) -> list[Path]:
    return sorted(migrations_dir(dialect).glob("[0-9]*.sql"))


def apply_migrations(conn, dialect: str) -> list[str]:
    """Apply unrecorded migrations; return the names applied now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
    )
    applied = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    done: list[str] = []
    for path in migration_files(dialect):
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.name,))
        done.append(path.name)
    conn.commit()
    return done

from __future__ import annotations

from pathlib import Path

from codesandbox.config import get_settings


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def apply_migrations(database_url: str | None = None) -> list[str]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL migrations require psycopg. Install project dependencies first."
        ) from exc

    settings = get_settings()
    url = database_url or settings.database_url
    applied: list[str] = []

    with psycopg.connect(url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_versions (
                  version text PRIMARY KEY,
                  applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = migration.name
                cursor.execute(
                    "SELECT 1 FROM migration_versions WHERE version = %s",
                    (version,),
                )
                if cursor.fetchone():
                    continue

                cursor.execute(migration.read_text())
                cursor.execute(
                    "INSERT INTO migration_versions (version) VALUES (%s)",
                    (version,),
                )
                applied.append(version)
        conn.commit()

    return applied

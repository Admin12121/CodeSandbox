from __future__ import annotations

import nexorm.database as nexorm_db


def configure_db(
    database_url: str,
    *,
    pool_size: int | None = None,
    pool_timeout: float | None = None,
) -> None:
    options = {}
    if pool_size is not None:
        options["pool_size"] = pool_size
    if pool_timeout is not None:
        options["pool_timeout"] = pool_timeout
    nexorm_db.configure(database_url, **options)


def get_db() -> nexorm_db.Database:
    return nexorm_db.get_connection("default")

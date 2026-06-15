from __future__ import annotations

import nexorm.database as nexorm_db


def configure_db(database_url: str) -> None:
    nexorm_db.configure(database_url)


def get_db() -> nexorm_db.Database:
    return nexorm_db.get_connection("default")

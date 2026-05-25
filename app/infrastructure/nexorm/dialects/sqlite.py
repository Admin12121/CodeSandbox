from .base import BaseDialect


class SQLiteDialect(BaseDialect):
    name = "sqlite"
    placeholder = "?"
    auto_increment = "AUTOINCREMENT"

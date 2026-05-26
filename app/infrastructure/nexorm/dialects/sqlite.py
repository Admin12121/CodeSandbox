from .base import BaseDialect


class SQLiteDialect(BaseDialect):
    name = "sqlite"
    placeholder = "?"
    auto_increment = "AUTOINCREMENT"
    requires_table_rebuild = True

    def disable_foreign_key_checks_sql(self):
        return ["PRAGMA foreign_keys = OFF"]

    def enable_foreign_key_checks_sql(self):
        return ["PRAGMA foreign_keys = ON"]

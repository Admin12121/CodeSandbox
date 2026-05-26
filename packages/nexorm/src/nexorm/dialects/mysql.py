from .base import BaseDialect


class MySQLDialect(BaseDialect):
    name = "mysql"
    placeholder = "%s"
    auto_increment = "AUTO_INCREMENT"
    identifier_quote = "`"
    type_map = {
        "integer": "INT",
        "string": "VARCHAR",
        "text": "TEXT",
        "boolean": "TINYINT(1)",
        "datetime": "DATETIME",
        "float": "DOUBLE",
        "decimal": "DECIMAL",
        "uuid": "CHAR(36)",
    }

    def auto_primary_key_sql(self):
        return "INT PRIMARY KEY AUTO_INCREMENT"

    def create_index_sql(self, table, name, columns, unique=False):
        kind = "UNIQUE INDEX" if unique else "INDEX"
        quoted_columns = ", ".join(self.quote_identifier(column) for column in columns)
        return (
            f"CREATE {kind} {self.quote_identifier(name)} "
            f"ON {self.quote_identifier(table)} ({quoted_columns})"
        )

    def insert_default_values_sql(self, table):
        return f"INSERT INTO {self.quote_identifier(table)} () VALUES ()"

    def drop_index_sql(self, name, table=None):
        if table is None:
            raise ValueError("MySQL requires a table name when dropping an index")
        return f"DROP INDEX {self.quote_identifier(name)} ON {self.quote_identifier(table)}"

    def alter_column_sql(self, table, old_column, new_column):
        return [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"MODIFY COLUMN {self.column_sql_from_state(new_column)}"
        ]

    def drop_foreign_key_sql(self, table, state):
        name = state.get("name") or f"fk_{table}_{state['column']}"
        return [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"DROP FOREIGN KEY {self.quote_identifier(name)}"
        ]

    def migration_history_table_sql(self, table="nexorm_migrations"):
        return (
            f"CREATE TABLE IF NOT EXISTS {self.quote_identifier(table)} ("
            f"{self.quote_identifier('id')} {self.auto_primary_key_sql()}, "
            f"{self.quote_identifier('name')} VARCHAR(255) NOT NULL UNIQUE, "
            f"{self.quote_identifier('applied_at')} DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )

    def disable_foreign_key_checks_sql(self):
        return ["SET FOREIGN_KEY_CHECKS = 0"]

    def enable_foreign_key_checks_sql(self):
        return ["SET FOREIGN_KEY_CHECKS = 1"]

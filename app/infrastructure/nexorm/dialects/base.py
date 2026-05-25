import re

from nexorm.exceptions import ConfigurationError


class BaseDialect:
    name = "base"
    placeholder = "?"
    auto_increment = "AUTOINCREMENT"
    identifier_quote = '"'
    identifier_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    type_map = {
        "integer": "INTEGER",
        "string": "VARCHAR",
        "text": "TEXT",
        "boolean": "BOOLEAN",
        "datetime": "DATETIME",
        "float": "REAL",
        "decimal": "DECIMAL",
    }

    def sql_type(self, field):
        base = self.type_map[field.type_name]
        if field.type_name == "string" and field.max_length:
            return f"{base}({field.max_length})"
        if field.type_name == "decimal":
            return f"{base}({field.max_digits},{field.decimal_places})"
        return base

    def column_sql(self, field):
        parts = [self.quote_identifier(field.name), self.sql_type(field)]
        if field.primary_key:
            parts.append("PRIMARY KEY")
        if field.auto_increment:
            parts.append(self.auto_increment)
        if not field.nullable and not field.primary_key:
            parts.append("NOT NULL")
        if field.unique:
            parts.append("UNIQUE")
        if field.default is not None and not callable(field.default):
            parts.append(f"DEFAULT {self.literal(field.default)}")
        return " ".join(parts)

    def literal(self, value):
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def foreign_key_sql(self, field):
        target_table, target_field = field.target_table_field()
        return (
            f"FOREIGN KEY ({self.quote_identifier(field.name)}) "
            f"REFERENCES {self.quote_identifier(target_table)}({self.quote_identifier(target_field)}) "
            f"ON DELETE {field.on_delete}"
        )

    def create_index_sql(self, table, name, columns, unique=False):
        kind = "UNIQUE INDEX" if unique else "INDEX"
        quoted_columns = ", ".join(self.quote_identifier(column) for column in columns)
        return (
            f"CREATE {kind} IF NOT EXISTS {self.quote_identifier(name)} "
            f"ON {self.quote_identifier(table)} ({quoted_columns})"
        )

    def validate_identifier(self, name):
        if not isinstance(name, str) or not self.identifier_re.match(name):
            raise ConfigurationError(f"Unsafe SQL identifier: {name!r}")
        return name

    def quote_identifier(self, name):
        name = self.validate_identifier(name)
        quote = self.identifier_quote
        escaped = name.replace(quote, quote + quote)
        return f"{quote}{escaped}{quote}"

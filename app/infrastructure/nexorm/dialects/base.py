import re

from nexorm.exceptions import ConfigurationError


class BaseDialect:
    name = "base"
    placeholder = "?"
    auto_increment = "AUTOINCREMENT"
    identifier_quote = '"'
    requires_table_rebuild = False
    supports_insert_returning = False
    identifier_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    type_map = {
        "integer": "INTEGER",
        "string": "VARCHAR",
        "text": "TEXT",
        "boolean": "BOOLEAN",
        "datetime": "DATETIME",
        "float": "REAL",
        "decimal": "DECIMAL",
        "uuid": "TEXT",
    }
    field_type_map = {
        "IntegerField": "integer",
        "ForeignKey": "integer",
        "StringField": "string",
        "TextField": "text",
        "BooleanField": "boolean",
        "DateTimeField": "datetime",
        "FloatField": "float",
        "DecimalField": "decimal",
        "UUIDField": "uuid",
    }

    def sql_type(self, field):
        return self.sql_type_from_state(self.field_state(field))

    def sql_type_from_state(self, column):
        field = column.get("field", column)
        field_type = field.get("type") or column.get("type")
        if field_type == "ForeignKey" and field.get("target_field"):
            field = field["target_field"]
            field_type = field.get("type")
        type_name = self.field_type_map.get(
            field_type, field.get("type_name") or column.get("type_name")
        )
        if type_name is None:
            raise ConfigurationError(
                f"Unknown field type for column {column.get('name')!r}: {field_type!r}"
            )
        base = self.type_map[type_name]
        if type_name == "string":
            return f"{base}({field.get('max_length') or column.get('max_length') or 255})"
        if type_name == "decimal":
            max_digits = field.get("max_digits") or column.get("max_digits") or 10
            decimal_places = field.get("decimal_places") or column.get("decimal_places") or 2
            return f"{base}({max_digits},{decimal_places})"
        return base

    def column_sql(self, field):
        return self.column_sql_from_state(self.field_state(field))

    def column_sql_from_state(self, column):
        field = column.get("field", column)
        name = column["name"]
        primary_key = bool(field.get("primary_key", column.get("primary_key", False)))
        auto_increment = bool(field.get("auto_increment", column.get("auto_increment", False)))
        if primary_key and auto_increment:
            parts = [self.quote_identifier(name), self.auto_primary_key_sql()]
        else:
            parts = [self.quote_identifier(name), self.sql_type_from_state(column)]
        if primary_key and not auto_increment:
            parts.append("PRIMARY KEY")
        if auto_increment and not primary_key:
            parts.append(self.auto_increment)
        if not field.get("nullable", column.get("nullable", False)) and not primary_key:
            parts.append("NOT NULL")
        if field.get("unique", column.get("unique", False)):
            parts.append("UNIQUE")
        default = field.get("default", column.get("default"))
        if default is not None:
            parts.append(f"DEFAULT {self.literal(default)}")
        return " ".join(parts)

    def auto_primary_key_sql(self):
        return f"{self.type_map['integer']} PRIMARY KEY {self.auto_increment}".strip()

    def field_state(self, field):
        data = field.deconstruct()
        data.setdefault("type", field.__class__.__name__)
        if data["type"] == "ForeignKey":
            try:
                data["target_field"] = field.target_field().deconstruct()
            except ConfigurationError:
                pass
        return {
            "name": field.name,
            "field": data,
            "type": data["type"],
            "nullable": data.get("nullable", False),
            "unique": data.get("unique", False),
            "default": data.get("default"),
            "primary_key": data.get("primary_key", False),
            "index": data.get("index", False),
            "auto_increment": data.get("auto_increment", False),
            "max_length": data.get("max_length"),
            "max_digits": data.get("max_digits"),
            "decimal_places": data.get("decimal_places"),
        }

    def literal(self, value):
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def foreign_key_sql(self, field):
        target_table, target_field = field.target_table_field()
        return self.foreign_key_sql_from_state(
            {
                "column": field.name,
                "to_table": target_table,
                "to_column": target_field,
                "on_delete": field.on_delete,
            }
        )

    def foreign_key_sql_from_state(self, state):
        from nexorm.fields import normalize_on_delete

        column = state["column"]
        target_table = state["to_table"]
        target_field = state["to_column"]
        on_delete = normalize_on_delete(state.get("on_delete", "CASCADE"))
        prefix = ""
        if state.get("name"):
            prefix = f"CONSTRAINT {self.quote_identifier(state['name'])} "
        return (
            f"{prefix}FOREIGN KEY ({self.quote_identifier(column)}) "
            f"REFERENCES {self.quote_identifier(target_table)}"
            f"({self.quote_identifier(target_field)}) "
            f"ON DELETE {on_delete}"
        )

    def create_index_sql(self, table, name, columns, unique=False):
        kind = "UNIQUE INDEX" if unique else "INDEX"
        quoted_columns = ", ".join(self.quote_identifier(column) for column in columns)
        return (
            f"CREATE {kind} IF NOT EXISTS {self.quote_identifier(name)} "
            f"ON {self.quote_identifier(table)} ({quoted_columns})"
        )

    def insert_default_values_sql(self, table):
        return f"INSERT INTO {self.quote_identifier(table)} DEFAULT VALUES"

    def drop_index_sql(self, name, table=None):
        return f"DROP INDEX IF EXISTS {self.quote_identifier(name)}"

    def drop_column_sql(self, table, column):
        return [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"DROP COLUMN {self.quote_identifier(column['name'])}"
        ]

    def alter_column_sql(self, table, old_column, new_column):
        name = new_column["name"]
        sql_type = self.sql_type_from_state(new_column)
        statements = [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"ALTER COLUMN {self.quote_identifier(name)} TYPE {sql_type}"
        ]
        if old_column.get("nullable") != new_column.get("nullable"):
            action = "DROP NOT NULL" if new_column.get("nullable") else "SET NOT NULL"
            statements.append(
                f"ALTER TABLE {self.quote_identifier(table)} "
                f"ALTER COLUMN {self.quote_identifier(name)} {action}"
            )
        if old_column.get("default") != new_column.get("default"):
            default = new_column.get("default")
            action = (
                f"SET DEFAULT {self.literal(default)}"
                if default is not None
                else "DROP DEFAULT"
            )
            statements.append(
                f"ALTER TABLE {self.quote_identifier(table)} "
                f"ALTER COLUMN {self.quote_identifier(name)} {action}"
            )
        return statements

    def add_foreign_key_sql(self, table, state):
        name = state.get("name") or f"fk_{table}_{state['column']}"
        state = {**state, "name": name}
        return [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"ADD {self.foreign_key_sql_from_state(state)}"
        ]

    def drop_foreign_key_sql(self, table, state):
        name = state.get("name") or f"fk_{table}_{state['column']}"
        return [
            f"ALTER TABLE {self.quote_identifier(table)} "
            f"DROP CONSTRAINT {self.quote_identifier(name)}"
        ]

    def migration_history_table_sql(self, table="nexorm_migrations"):
        return (
            f"CREATE TABLE IF NOT EXISTS {self.quote_identifier(table)} ("
            f"{self.quote_identifier('id')} {self.auto_primary_key_sql()}, "
            f"{self.quote_identifier('name')} VARCHAR(255) NOT NULL UNIQUE, "
            f"{self.quote_identifier('applied_at')} DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )

    def disable_foreign_key_checks_sql(self):
        return []

    def enable_foreign_key_checks_sql(self):
        return []

    def validate_identifier(self, name):
        if not isinstance(name, str) or not self.identifier_re.match(name):
            raise ConfigurationError(f"Unsafe SQL identifier: {name!r}")
        return name

    def quote_identifier(self, name):
        name = self.validate_identifier(name)
        quote = self.identifier_quote
        escaped = name.replace(quote, quote + quote)
        return f"{quote}{escaped}{quote}"

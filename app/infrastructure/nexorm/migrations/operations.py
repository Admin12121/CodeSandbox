from nexorm.dialects.sqlite import SQLiteDialect


class Operation:
    def to_sql(self, dialect=None):
        raise NotImplementedError

    def reverse_sql(self, dialect=None):
        raise NotImplementedError

    def describe(self):
        return self.__class__.__name__


class CreateTable(Operation):
    def __init__(self, name, columns, foreign_keys=None, indexes=None):
        self.name = name
        self.columns = columns
        self.foreign_keys = foreign_keys or []
        self.indexes = indexes or []

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        parts = [_column_sql(column, dialect) for column in self.columns]
        parts.extend(_foreign_key_sql(foreign_key, dialect) for foreign_key in self.foreign_keys)
        sql = [
            f"CREATE TABLE IF NOT EXISTS {dialect.quote_identifier(self.name)} "
            f"({', '.join(parts)})"
        ]
        for index in self.indexes:
            sql.append(
                dialect.create_index_sql(
                    self.name, index["name"], index["columns"], index.get("unique", False)
                )
            )
        return sql

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [f"DROP TABLE IF EXISTS {dialect.quote_identifier(self.name)}"]

    def describe(self):
        return f"Create table {self.name}"


class DropTable(Operation):
    def __init__(self, name, table_state=None):
        self.name = name
        self.table_state = table_state

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [f"DROP TABLE IF EXISTS {dialect.quote_identifier(self.name)}"]

    def reverse_sql(self, dialect=None):
        if not self.table_state:
            raise ValueError(f"Cannot reverse DropTable({self.name!r}) without table_state")
        return _create_table_sql(self.name, self.table_state, dialect or SQLiteDialect())

    def describe(self):
        return f"Drop table {self.name}"


class AddColumn(Operation):
    def __init__(self, table, column, old_table=None, new_table=None):
        self.table = table
        self.column = column
        self.old_table = old_table
        self.new_table = new_table

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [
            f"ALTER TABLE {dialect.quote_identifier(self.table)} "
            f"ADD COLUMN {_column_sql(self.column, dialect)}"
        ]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if not dialect.requires_table_rebuild:
            return dialect.drop_column_sql(self.table, self.column)
        if not self.old_table or not self.new_table:
            raise ValueError(
                f"Cannot reverse AddColumn({self.table!r}) without old/new table state"
            )
        return _rebuild_table_sql(self.table, self.new_table, self.old_table, dialect)

    def describe(self):
        return f"Add column {self.column['name']} to {self.table}"


class RemoveColumn(Operation):
    requires_foreign_key_disable = True

    def __init__(self, table, column, old_table=None, new_table=None):
        self.table = table
        self.column = column
        self.old_table = old_table
        self.new_table = new_table

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if not dialect.requires_table_rebuild:
            return dialect.drop_column_sql(self.table, self.column)
        if not self.old_table or not self.new_table:
            raise ValueError(
                f"Cannot apply RemoveColumn({self.table!r}) without old/new table state"
            )
        return _rebuild_table_sql(self.table, self.old_table, self.new_table, dialect)

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if not dialect.requires_table_rebuild:
            return [
                f"ALTER TABLE {dialect.quote_identifier(self.table)} "
                f"ADD COLUMN {_column_sql(self.column, dialect)}"
            ]
        if not self.old_table or not self.new_table:
            raise ValueError(
                f"Cannot reverse RemoveColumn({self.table!r}) without old/new table state"
            )
        return _rebuild_table_sql(self.table, self.new_table, self.old_table, dialect)

    def describe(self):
        column = self.column["name"] if isinstance(self.column, dict) else self.column
        return f"Remove column {column} from {self.table}"


class AlterColumn(Operation):
    requires_foreign_key_disable = True

    def __init__(self, table, old_column, new_column, old_table=None, new_table=None):
        self.table = table
        self.old_column = old_column
        self.new_column = new_column
        self.old_table = old_table
        self.new_table = new_table

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if not dialect.requires_table_rebuild:
            return dialect.alter_column_sql(self.table, self.old_column, self.new_column)
        if not self.old_table or not self.new_table:
            raise ValueError(
                f"Cannot apply AlterColumn({self.table!r}) without old/new table state"
            )
        return _rebuild_table_sql(self.table, self.old_table, self.new_table, dialect)

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if not dialect.requires_table_rebuild:
            return dialect.alter_column_sql(self.table, self.new_column, self.old_column)
        if not self.old_table or not self.new_table:
            raise ValueError(
                f"Cannot reverse AlterColumn({self.table!r}) without old/new table state"
            )
        return _rebuild_table_sql(self.table, self.new_table, self.old_table, dialect)

    def describe(self):
        return f"Alter column {self.new_column['name']} on {self.table}"


class RenameTable(Operation):
    def __init__(self, old_name, new_name):
        self.old_name = old_name
        self.new_name = new_name

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [
            f"ALTER TABLE {dialect.quote_identifier(self.old_name)} "
            f"RENAME TO {dialect.quote_identifier(self.new_name)}"
        ]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [
            f"ALTER TABLE {dialect.quote_identifier(self.new_name)} "
            f"RENAME TO {dialect.quote_identifier(self.old_name)}"
        ]


class RenameColumn(Operation):
    def __init__(self, table, old_name, new_name):
        self.table = table
        self.old_name = old_name
        self.new_name = new_name

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [
            f"ALTER TABLE {dialect.quote_identifier(self.table)} "
            f"RENAME COLUMN {dialect.quote_identifier(self.old_name)} "
            f"TO {dialect.quote_identifier(self.new_name)}"
        ]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [
            f"ALTER TABLE {dialect.quote_identifier(self.table)} "
            f"RENAME COLUMN {dialect.quote_identifier(self.new_name)} "
            f"TO {dialect.quote_identifier(self.old_name)}"
        ]


class CreateIndex(Operation):
    def __init__(self, table, name, columns, unique=False):
        self.table = table
        self.name = name
        self.columns = columns
        self.unique = unique

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [dialect.create_index_sql(self.table, self.name, self.columns, self.unique)]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [dialect.drop_index_sql(self.name, self.table)]


class DropIndex(Operation):
    def __init__(self, name, table=None, columns=None, unique=False):
        self.name = name
        self.table = table
        self.columns = columns or []
        self.unique = unique

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        return [dialect.drop_index_sql(self.name, self.table)]

    def reverse_sql(self, dialect=None):
        if not self.table or not self.columns:
            raise ValueError(f"Cannot reverse DropIndex({self.name!r}) without table/columns")
        dialect = dialect or SQLiteDialect()
        return [dialect.create_index_sql(self.table, self.name, self.columns, self.unique)]


class AddForeignKey(Operation):
    requires_foreign_key_disable = True

    def __init__(self, table, old_table, new_table):
        self.table = table
        self.old_table = old_table
        self.new_table = new_table

    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if dialect.requires_table_rebuild:
            return _rebuild_table_sql(self.table, self.old_table, self.new_table, dialect)
        return [
            sql
            for foreign_key in _foreign_key_added(self.old_table, self.new_table)
            for sql in dialect.add_foreign_key_sql(self.table, foreign_key)
        ]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if dialect.requires_table_rebuild:
            return _rebuild_table_sql(self.table, self.new_table, self.old_table, dialect)
        return [
            sql
            for foreign_key in _foreign_key_added(self.old_table, self.new_table)
            for sql in dialect.drop_foreign_key_sql(self.table, foreign_key)
        ]


class RemoveForeignKey(AddForeignKey):
    def to_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if dialect.requires_table_rebuild:
            return _rebuild_table_sql(self.table, self.old_table, self.new_table, dialect)
        return [
            sql
            for foreign_key in _foreign_key_added(self.new_table, self.old_table)
            for sql in dialect.drop_foreign_key_sql(self.table, foreign_key)
        ]

    def reverse_sql(self, dialect=None):
        dialect = dialect or SQLiteDialect()
        if dialect.requires_table_rebuild:
            return _rebuild_table_sql(self.table, self.new_table, self.old_table, dialect)
        return [
            sql
            for foreign_key in _foreign_key_added(self.new_table, self.old_table)
            for sql in dialect.add_foreign_key_sql(self.table, foreign_key)
        ]


def _create_table_sql(name, table_state, dialect):
    columns = list(table_state["columns"].values())
    return CreateTable(
        name, columns, table_state.get("foreign_keys", []), table_state.get("indexes", [])
    ).to_sql(dialect)


def _rebuild_table_sql(table, from_state, to_state, dialect):
    temp_table = f"__nexorm_tmp_{table}"
    common_columns = [name for name in to_state["columns"] if name in from_state["columns"]]
    parts = [_column_sql(column, dialect) for column in to_state["columns"].values()]
    parts.extend(
        _foreign_key_sql(foreign_key, dialect) for foreign_key in to_state.get("foreign_keys", [])
    )
    quoted_temp = dialect.quote_identifier(temp_table)
    quoted_table = dialect.quote_identifier(table)
    sql = [
        f"DROP TABLE IF EXISTS {quoted_temp}",
        f"CREATE TABLE {quoted_temp} ({', '.join(parts)})",
    ]
    if common_columns:
        columns = ", ".join(dialect.quote_identifier(column) for column in common_columns)
        sql.append(f"INSERT INTO {quoted_temp} ({columns}) SELECT {columns} FROM {quoted_table}")
    sql.extend(
        [
            f"DROP TABLE {quoted_table}",
            f"ALTER TABLE {quoted_temp} RENAME TO {quoted_table}",
        ]
    )
    for index in to_state.get("indexes", []):
        sql.append(
            dialect.create_index_sql(
                table, index["name"], index["columns"], index.get("unique", False)
            )
        )
    return sql


def _column_sql(column, dialect):
    if "field" not in column and "sql" in column and dialect.name == "sqlite":
        return column["sql"]
    if "field" in column or "type" in column:
        try:
            return dialect.column_sql_from_state(column)
        except Exception:
            if "sql" in column:
                return column["sql"]
            raise
    return column["sql"]


def _foreign_key_sql(foreign_key, dialect):
    if isinstance(foreign_key, str):
        return foreign_key
    return dialect.foreign_key_sql_from_state(foreign_key)


def _foreign_key_key(foreign_key):
    if isinstance(foreign_key, str):
        return foreign_key
    return (
        foreign_key.get("column"),
        foreign_key.get("to_table"),
        foreign_key.get("to_column"),
        foreign_key.get("on_delete"),
    )


def _foreign_key_added(old_table, new_table):
    old_keys = {_foreign_key_key(foreign_key) for foreign_key in old_table.get("foreign_keys", [])}
    return [
        foreign_key
        for foreign_key in new_table.get("foreign_keys", [])
        if _foreign_key_key(foreign_key) not in old_keys
    ]

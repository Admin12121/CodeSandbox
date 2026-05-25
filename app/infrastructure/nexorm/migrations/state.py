import json
from pathlib import Path
from nexorm.dialects.sqlite import SQLiteDialect
from nexorm.fields import ForeignKey
from nexorm.registry import get_models


def model_state(dialect=None):
    dialect = dialect or SQLiteDialect()
    tables = {}
    for model in get_models():
        meta = model._meta
        columns = {}
        for name, field in meta.fields.items():
            columns[name] = {
                "name": name,
                "type": field.__class__.__name__,
                "sql_type": dialect.sql_type(field),
                "sql": dialect.column_sql(field),
                "nullable": field.nullable,
                "unique": field.unique,
                "default": None if callable(field.default) else field.default,
                "primary_key": field.primary_key,
                "index": field.index,
                "auto_increment": field.auto_increment,
                "foreign_key": field.deconstruct() if isinstance(field, ForeignKey) else None,
            }
        tables[meta.table_name] = {
            "name": meta.table_name,
            "model": meta.model_name,
            "columns": columns,
            "indexes": [
                {"name": name, "columns": cols, "unique": unique}
                for name, cols, unique in meta.indexes
            ],
            "foreign_keys": [dialect.foreign_key_sql(field) for field in meta.foreign_keys],
        }
    return {"tables": tables}


def read_state(path="migrations/schema_state.json"):
    file = Path(path)
    if not file.exists():
        return {"tables": {}}
    return json.loads(file.read_text())


def write_state(state, path="migrations/schema_state.json"):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

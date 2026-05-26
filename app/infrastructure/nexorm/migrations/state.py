import json
from pathlib import Path
from nexorm.fields import ForeignKey
from nexorm.registry import get_models


def model_state(dialect=None):
    tables = {}
    for model in get_models():
        meta = model._meta
        columns = {}
        for name, field in meta.fields.items():
            field_data = field.deconstruct()
            columns[name] = {
                "name": name,
                "field": field_data,
                "type": field.__class__.__name__,
                "nullable": field.nullable,
                "unique": field.unique,
                "default": None if callable(field.default) else field.default,
                "primary_key": field.primary_key,
                "index": field.index,
                "auto_increment": field.auto_increment,
                "max_length": field_data.get("max_length"),
                "max_digits": field_data.get("max_digits"),
                "decimal_places": field_data.get("decimal_places"),
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
            "foreign_keys": [_foreign_key_state(meta.table_name, field) for field in meta.foreign_keys],
        }
    return {"tables": tables}


def _foreign_key_state(table, field):
    target_table, target_field = field.target_table_field()
    return {
        "name": f"fk_{table}_{field.name}",
        "column": field.name,
        "to_table": target_table,
        "to_column": target_field,
        "on_delete": field.on_delete,
    }


def read_state(path="migrations/schema_state.json"):
    file = Path(path)
    if not file.exists():
        return {"tables": {}}
    return json.loads(file.read_text())


def write_state(state, path="migrations/schema_state.json"):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

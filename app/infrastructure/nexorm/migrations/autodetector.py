from nexorm.migrations.operations import (
    AddColumn,
    AddForeignKey,
    AlterColumn,
    CreateIndex,
    CreateTable,
    DropIndex,
    DropTable,
    RemoveColumn,
    RemoveForeignKey,
)


class MigrationAutodetector:
    def __init__(self, old_state, new_state):
        self.old_state = old_state
        self.new_state = new_state

    def changes(self):
        ops = []
        old_tables = self.old_state.get("tables", {})
        new_tables = self.new_state.get("tables", {})
        for name, table in new_tables.items():
            if name not in old_tables:
                ops.append(
                    CreateTable(
                        name,
                        list(table["columns"].values()),
                        table["foreign_keys"],
                        table["indexes"],
                    )
                )
                continue
            old_cols = old_tables[name]["columns"]
            new_cols = table["columns"]
            old_foreign_keys = {
                _foreign_key_key(fk): fk for fk in old_tables[name].get("foreign_keys", [])
            }
            new_foreign_keys = {_foreign_key_key(fk): fk for fk in table.get("foreign_keys", [])}
            if any(key not in new_foreign_keys for key in old_foreign_keys):
                ops.append(RemoveForeignKey(name, old_tables[name], table))
            for col, data in new_cols.items():
                if col not in old_cols:
                    ops.append(AddColumn(name, data, old_tables[name], table))
                elif self._column_changed(old_cols[col], data):
                    ops.append(AlterColumn(name, old_cols[col], data, old_tables[name], table))
            for col in old_cols:
                if col not in new_cols:
                    ops.append(RemoveColumn(name, old_cols[col], old_tables[name], table))
            old_indexes = {idx["name"]: idx for idx in old_tables[name].get("indexes", [])}
            new_indexes = {idx["name"]: idx for idx in table.get("indexes", [])}
            for idx in new_indexes.values():
                if idx["name"] not in old_indexes:
                    ops.append(
                        CreateIndex(name, idx["name"], idx["columns"], idx.get("unique", False))
                    )
            for idx in old_indexes:
                if idx not in new_indexes:
                    old_index = old_indexes[idx]
                    ops.append(
                        DropIndex(
                            idx, name, old_index["columns"], old_index.get("unique", False)
                        )
                    )
            if any(key not in old_foreign_keys for key in new_foreign_keys):
                ops.append(AddForeignKey(name, old_tables[name], table))
        for name in old_tables:
            if name not in new_tables:
                ops.append(DropTable(name, old_tables[name]))
        return ops

    @staticmethod
    def _column_changed(old, new):
        keys = [
            "type",
            "nullable",
            "unique",
            "default",
            "primary_key",
            "index",
            "auto_increment",
            "max_length",
            "max_digits",
            "decimal_places",
            "foreign_key",
        ]
        return any(key in old and old.get(key) != new.get(key) for key in keys)


def _foreign_key_key(foreign_key):
    if isinstance(foreign_key, str):
        return foreign_key
    return (
        foreign_key.get("column"),
        foreign_key.get("to_table"),
        foreign_key.get("to_column"),
        foreign_key.get("on_delete"),
    )

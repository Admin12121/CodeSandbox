from nexorm.migrations.operations import AlterColumn, AddColumn, CreateIndex, CreateTable, DropIndex, DropTable, RemoveColumn


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
                ops.append(CreateTable(name, list(table["columns"].values()), table["foreign_keys"], table["indexes"]))
                continue
            old_cols = old_tables[name]["columns"]
            new_cols = table["columns"]
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
                    ops.append(CreateIndex(name, idx["name"], idx["columns"], idx.get("unique", False)))
            for idx in old_indexes:
                if idx not in new_indexes:
                    old_index = old_indexes[idx]
                    ops.append(DropIndex(idx, name, old_index["columns"], old_index.get("unique", False)))
        for name in old_tables:
            if name not in new_tables:
                ops.append(DropTable(name, old_tables[name]))
        return ops

    @staticmethod
    def _column_changed(old, new):
        keys = ["sql_type", "nullable", "unique", "default", "primary_key", "index", "auto_increment", "foreign_key"]
        return any(old.get(key) != new.get(key) for key in keys)

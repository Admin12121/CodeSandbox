class Options:
    def __init__(self, model, table_name=None, indexes=None, unique_together=None):
        self.model = model
        self.model_name = model.__name__
        self.table_name = table_name or self.default_table_name(model.__name__)
        self.fields = {}
        self.primary_key = None
        self.indexes = []
        self.constraints = []
        self.foreign_keys = []
        self._declared_indexes = list(indexes or [])
        self._unique_together = list(unique_together or [])

    @staticmethod
    def default_table_name(name):
        out = []
        for idx, char in enumerate(name):
            if char.isupper() and idx:
                out.append("_")
            out.append(char.lower())
        return "".join(out) + "s"

    def add_field(self, name, field):
        field.name = name
        field.model = self.model
        self.fields[name] = field
        if field.primary_key:
            self.primary_key = field
        if field.index:
            self.indexes.append((f"idx_{self.table_name}_{name}", [name], False))
        if field.unique:
            self.indexes.append((f"uidx_{self.table_name}_{name}", [name], True))
        if hasattr(field, "to"):
            self.foreign_keys.append(field)

    def finalize_indexes(self):
        """Add model-level composite indexes after every field is registered."""
        for declared in self._declared_indexes:
            if isinstance(declared, dict):
                columns = declared.get("fields") or declared.get("columns") or []
                name = declared.get("name")
                unique = bool(declared.get("unique", False))
            else:
                try:
                    name, columns, unique = declared
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Meta.indexes entries must be (name, columns, unique) tuples or dictionaries"
                    ) from exc
            self._add_declared_index(name, columns, unique)

        for columns in self._unique_together:
            columns = list(columns)
            name = f"uidx_{self.table_name}_{'_'.join(columns)}"
            self._add_declared_index(name, columns, True)

    def _add_declared_index(self, name, columns, unique):
        columns = list(columns or [])
        if not name or not columns:
            raise ValueError("Composite indexes require a name and at least one field")
        unknown = [column for column in columns if column not in self.fields]
        if unknown:
            raise ValueError(
                f"Unknown field(s) in index {name!r}: {', '.join(unknown)}"
            )
        entry = (str(name), columns, bool(unique))
        if entry not in self.indexes:
            self.indexes.append(entry)

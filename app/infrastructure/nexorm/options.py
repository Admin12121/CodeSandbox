class Options:
    def __init__(self, model, table_name=None):
        self.model = model
        self.model_name = model.__name__
        self.table_name = table_name or self.default_table_name(model.__name__)
        self.fields = {}
        self.primary_key = None
        self.indexes = []
        self.constraints = []
        self.foreign_keys = []

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

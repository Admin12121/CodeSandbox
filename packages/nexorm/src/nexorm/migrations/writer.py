import re
from pathlib import Path


class MigrationWriter:
    def __init__(self, operations, target_state=None, migrations_dir="migrations"):
        self.operations = operations
        self.target_state = target_state or {"tables": {}}
        self.migrations_dir = Path(migrations_dir)

    def next_name(self, name=None):
        self.migrations_dir.mkdir(exist_ok=True)
        nums = []
        for path in self.migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py"):
            nums.append(int(path.name[:4]))
        number = max(nums, default=0) + 1
        label = name or self.suggest_name()
        return f"{number:04d}_{label}.py"

    def suggest_name(self):
        if not self.operations:
            return "empty"
        words = re.sub(r"[^a-zA-Z0-9]+", "_", self.operations[0].describe().lower()).strip("_")
        return words[:50] or "auto"

    def write(self, name=None):
        filename = self.next_name(name)
        path = self.migrations_dir / filename
        imports = "from nexorm.migrations.operations import *\n\n"
        body = "operations = [\n"
        for op in self.operations:
            body += f"    {repr_operation(op)},\n"
        body += "]\n"
        body += f"\nschema_state = {self.target_state!r}\n"
        path.write_text(imports + body)
        return path


def repr_operation(op):
    attrs = ", ".join(f"{key}={value!r}" for key, value in op.__dict__.items())
    return f"{op.__class__.__name__}({attrs})"

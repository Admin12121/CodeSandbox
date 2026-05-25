from nexorm.lookups import parse_lookup, prepare_value
from nexorm.exceptions import ConfigurationError


class Where:
    def __init__(self, connector="AND"):
        self.connector = connector
        self.children = []

    def add(self, key, value, negate=False):
        self.children.append((key, value, negate))

    def clone(self):
        other = Where(self.connector)
        other.children = list(self.children)
        return other

    def to_sql(self, model, dialect):
        clauses = []
        params = []
        for key, value, negate in self.children:
            field, lookup = parse_lookup(key)
            if field not in model._meta.fields:
                raise ConfigurationError(f"Unknown field for {model.__name__}: {field}")
            column = dialect.quote_identifier(field)
            ph = dialect.placeholder
            prefix = "NOT " if negate else ""
            if lookup == "in":
                if not value:
                    clauses.append("1 = 0" if not negate else "1 = 1")
                    continue
                placeholders = ", ".join([ph] * len(value))
                clauses.append(f"{prefix}{column} IN ({placeholders})")
                params.extend(value)
            elif lookup == "isnull":
                clauses.append(f"{column} IS {'NOT ' if bool(value) ^ negate else ''}NULL")
            else:
                clauses.append(f"{prefix}{column} {operator_for(lookup)} {ph}")
                params.append(prepare_value(lookup, value))
        if not clauses:
            return "", []
        return " WHERE " + f" {self.connector} ".join(clauses), params


def operator_for(lookup):
    return {
        "exact": "=",
        "contains": "LIKE",
        "startswith": "LIKE",
        "endswith": "LIKE",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }[lookup]

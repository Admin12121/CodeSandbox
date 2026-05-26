LOOKUPS = {
    "exact": "{field} = {ph}",
    "contains": "{field} LIKE {ph}",
    "startswith": "{field} LIKE {ph}",
    "endswith": "{field} LIKE {ph}",
    "gt": "{field} > {ph}",
    "gte": "{field} >= {ph}",
    "lt": "{field} < {ph}",
    "lte": "{field} <= {ph}",
    "in": "{field} IN ({ph})",
    "isnull": "{field} IS {neg}NULL",
}


def parse_lookup(key):
    parts = key.split("__")
    if len(parts) > 1 and parts[-1] in LOOKUPS:
        return "__".join(parts[:-1]), parts[-1]
    return key, "exact"


def prepare_value(lookup, value):
    if lookup == "contains":
        return f"%{value}%"
    if lookup == "startswith":
        return f"{value}%"
    if lookup == "endswith":
        return f"%{value}"
    return value

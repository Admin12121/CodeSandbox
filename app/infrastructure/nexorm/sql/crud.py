from nexorm.database import default_db


class CRUDEngine:
    def __init__(self, db=None, dialect=None):
        self.db = db or default_db
        self.dialect = dialect or self.db.dialect

    def insert(self, instance):
        meta = instance._meta
        fields = []
        values = []
        for name, field in meta.fields.items():
            value = getattr(instance, name, None)
            if field.primary_key and field.auto_increment and value is None:
                continue
            fields.append(name)
            values.append(field.to_db(value))
        ph = ", ".join([self.dialect.placeholder] * len(fields))
        columns = ", ".join(self.dialect.quote_identifier(field) for field in fields)
        sql = f"INSERT INTO {self.dialect.quote_identifier(meta.table_name)} ({columns}) VALUES ({ph})"
        cursor = self.db.execute(sql, values)
        if meta.primary_key and getattr(instance, meta.primary_key.name, None) is None:
            setattr(instance, meta.primary_key.name, cursor.lastrowid)
        if not self.db.in_atomic:
            self.db.commit()
        return instance

    def update(self, instance):
        meta = instance._meta
        pk = meta.primary_key
        fields = [name for name in meta.fields if name != pk.name]
        assignments = ", ".join([f"{self.dialect.quote_identifier(name)} = {self.dialect.placeholder}" for name in fields])
        values = [meta.fields[name].to_db(getattr(instance, name, None)) for name in fields]
        values.append(getattr(instance, pk.name))
        sql = (
            f"UPDATE {self.dialect.quote_identifier(meta.table_name)} SET {assignments} "
            f"WHERE {self.dialect.quote_identifier(pk.name)} = {self.dialect.placeholder}"
        )
        self.db.execute(sql, values)
        if not self.db.in_atomic:
            self.db.commit()
        return instance

    def delete(self, instance):
        meta = instance._meta
        pk = meta.primary_key
        self.db.execute(
            (
                f"DELETE FROM {self.dialect.quote_identifier(meta.table_name)} "
                f"WHERE {self.dialect.quote_identifier(pk.name)} = {self.dialect.placeholder}"
            ),
            [getattr(instance, pk.name)],
        )
        if not self.db.in_atomic:
            self.db.commit()

    def bulk_insert(self, instances):
        return [self.insert(instance) for instance in instances]

    def bulk_update(self, instances):
        return [self.update(instance) for instance in instances]

    def bulk_delete(self, instances):
        for instance in instances:
            self.delete(instance)

class SQLCompiler:
    def __init__(self, queryset):
        self.queryset = queryset

    def select(self):
        qs = self.queryset
        table = qs.dialect.quote_identifier(qs.model._meta.table_name)
        sql = f"SELECT * FROM {table}"
        where_sql, params = qs.where.to_sql(qs.model, qs.dialect)
        sql += where_sql
        if qs._order_by:
            pieces = []
            for name in qs._order_by:
                direction = "DESC" if name.startswith("-") else "ASC"
                field_name = name.lstrip("-")
                self._validate_field(field_name)
                pieces.append(f"{qs.dialect.quote_identifier(field_name)} {direction}")
            sql += " ORDER BY " + ", ".join(pieces)
        if qs._limit is not None:
            sql += f" LIMIT {int(qs._limit)}"
        if qs._offset is not None:
            sql += f" OFFSET {int(qs._offset)}"
        return sql, params

    def count(self):
        qs = self.queryset
        sql = f"SELECT COUNT(*) AS count FROM {qs.dialect.quote_identifier(qs.model._meta.table_name)}"
        where_sql, params = qs.where.to_sql(qs.model, qs.dialect)
        return sql + where_sql, params

    def delete(self):
        qs = self.queryset
        sql = f"DELETE FROM {qs.dialect.quote_identifier(qs.model._meta.table_name)}"
        where_sql, params = qs.where.to_sql(qs.model, qs.dialect)
        return sql + where_sql, params

    def update(self, values):
        qs = self.queryset
        for key in values:
            self._validate_field(key)
        assignments = ", ".join([f"{qs.dialect.quote_identifier(key)} = {qs.dialect.placeholder}" for key in values])
        params = [qs.model._meta.fields[key].to_db(value) for key, value in values.items()]
        where_sql, where_params = qs.where.to_sql(qs.model, qs.dialect)
        sql = f"UPDATE {qs.dialect.quote_identifier(qs.model._meta.table_name)} SET {assignments}{where_sql}"
        return sql, params + where_params

    def _validate_field(self, name):
        if name not in self.queryset.model._meta.fields:
            from nexorm.exceptions import ConfigurationError

            raise ConfigurationError(f"Unknown field for {self.queryset.model.__name__}: {name}")

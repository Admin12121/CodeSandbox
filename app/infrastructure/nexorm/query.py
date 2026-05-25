from nexorm.database import default_db
from nexorm.exceptions import DoesNotExist, MultipleObjectsReturned
from nexorm.sql.compiler import SQLCompiler
from nexorm.sql.expressions import Where


class QuerySet:
    def __init__(self, model, db=None, dialect=None, where=None):
        self.model = model
        self.db = db or default_db
        self.dialect = dialect or self.db.dialect
        self.where = where or Where()
        self._order_by = []
        self._limit = None
        self._offset = None

    def clone(self):
        other = self.__class__(self.model, self.db, self.dialect, self.where.clone())
        other._order_by = list(self._order_by)
        other._limit = self._limit
        other._offset = self._offset
        return other

    def all(self):
        sql, params = SQLCompiler(self).select()
        return [self.model.from_row(row) for row in self.db.fetchall(sql, params)]

    def first(self):
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def get(self, **kwargs):
        qs = self.filter(**kwargs) if kwargs else self
        rows = qs.limit(2).all()
        if not rows:
            raise DoesNotExist(f"{self.model.__name__} matching query does not exist")
        if len(rows) > 1:
            raise MultipleObjectsReturned(f"get() returned more than one {self.model.__name__}")
        return rows[0]

    def filter(self, **kwargs):
        qs = self.clone()
        for key, value in kwargs.items():
            qs.where.add(key, value)
        return qs

    def exclude(self, **kwargs):
        qs = self.clone()
        for key, value in kwargs.items():
            qs.where.add(key, value, negate=True)
        return qs

    def order_by(self, *fields):
        qs = self.clone()
        qs._order_by = list(fields)
        return qs

    def limit(self, value):
        qs = self.clone()
        qs._limit = value
        return qs

    def offset(self, value):
        qs = self.clone()
        qs._offset = value
        return qs

    def count(self):
        sql, params = SQLCompiler(self).count()
        row = self.db.fetchone(sql, params)
        return row["count"] if row else 0

    def exists(self):
        return self.limit(1).count() > 0

    def update(self, **values):
        sql, params = SQLCompiler(self).update(values)
        cur = self.db.execute(sql, params)
        if not self.db.in_atomic:
            self.db.commit()
        return cur.rowcount

    def delete(self):
        sql, params = SQLCompiler(self).delete()
        cur = self.db.execute(sql, params)
        if not self.db.in_atomic:
            self.db.commit()
        return cur.rowcount

    def __iter__(self):
        return iter(self.all())

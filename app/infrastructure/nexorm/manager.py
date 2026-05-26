from nexorm.database import default_db, get_connection
from nexorm.query import QuerySet
from nexorm.raw import RawQuery


class Manager:
    def __init__(self):
        self.model = None

    def __get__(self, instance, owner):
        self.model = owner
        return self

    def get_queryset(self):
        return QuerySet(self.model)

    def using(self, alias_or_db):
        db = get_connection(alias_or_db) if isinstance(alias_or_db, str) else alias_or_db
        return QuerySet(self.model, db=db)

    def create(self, **kwargs):
        instance = self.model(**kwargs)
        instance.save()
        return instance

    def all(self):
        return self.get_queryset().all()

    def filter(self, **kwargs):
        return self.get_queryset().filter(**kwargs)

    def exclude(self, **kwargs):
        return self.get_queryset().exclude(**kwargs)

    def get(self, **kwargs):
        return self.get_queryset().get(**kwargs)

    def count(self):
        return self.get_queryset().count()

    def raw(self, sql, params=None):
        return RawQuery(self.model, sql, params, default_db)

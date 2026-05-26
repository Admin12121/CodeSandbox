from contextlib import contextmanager
from nexorm.database import default_db, get_connection


class transaction:
    @staticmethod
    @contextmanager
    def atomic(alias_or_db=None):
        db = default_db
        if alias_or_db is not None:
            db = get_connection(alias_or_db) if isinstance(alias_or_db, str) else alias_or_db
        with db.transaction():
            yield

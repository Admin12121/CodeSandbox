from contextlib import contextmanager
from nexorm.database import default_db


class transaction:
    @staticmethod
    @contextmanager
    def atomic():
        with default_db.transaction():
            yield

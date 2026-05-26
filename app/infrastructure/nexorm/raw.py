from nexorm.database import default_db
from nexorm.exceptions import ConfigurationError


class RawQuery:
    def __init__(self, model, sql, params=None, db=None):
        if params is None:
            raise ConfigurationError("Raw queries require an explicit params sequence")
        self.model = model
        self.sql = sql
        self.params = list(params)
        self.db = db or default_db

    def all(self):
        return [
            self.model.from_row(row, db=self.db)
            for row in self.db.fetchall(self.sql, self.params)
        ]

    def first(self):
        row = self.db.fetchone(self.sql, self.params)
        return self.model.from_row(row, db=self.db) if row else None

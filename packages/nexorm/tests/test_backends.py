import os
import time
from uuid import UUID

import pytest

from nexorm import ForeignKey, Model, StringField, configure, transaction
from nexorm.exceptions import ConfigurationError
from nexorm.migrations.engine import MigrationEngine
from nexorm.migrations.operations import CreateTable
from nexorm.migrations.state import model_state


class BackendAuthor(Model):
    username = StringField(max_length=80)

    class Meta:
        table_name = "backend_authors"


class BackendEntry(Model):
    title = StringField(max_length=80)
    author_id = ForeignKey("BackendAuthor", related_name="backend_entries")

    class Meta:
        table_name = "backend_entries"


def wait_for(db):
    last_error = None
    for _ in range(90):
        try:
            db.connect()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"database did not become ready: {last_error}")


def reset_table(model, db):
    db.execute(f"DROP TABLE IF EXISTS {db.dialect.quote_identifier(model._meta.table_name)}")
    db.commit()


def create_model_table(model, db):
    table = model_state()["tables"][model._meta.table_name]
    operation = CreateTable(
        model._meta.table_name,
        list(table["columns"].values()),
        table["foreign_keys"],
        table["indexes"],
    )
    for sql in operation.to_sql(db.dialect):
        db.execute(sql)
    db.commit()


@pytest.mark.parametrize(
    ("name", "env_var"),
    [
        ("postgresql", "NEXORM_POSTGRES_URL"),
        ("mysql", "NEXORM_MYSQL_URL"),
    ],
)
def test_backend_crud_transactions_relations_and_raw_sql(name, env_var):
    url = os.getenv(env_var)
    if not url:
        pytest.skip(f"{env_var} is not configured")

    db = configure(url, alias=f"integration_{name}")
    wait_for(db)
    MigrationEngine(db=db).ensure_history()
    reset_table(BackendEntry, db)
    reset_table(BackendAuthor, db)
    create_model_table(BackendAuthor, db)
    create_model_table(BackendEntry, db)

    author = BackendAuthor.objects.using(db).create(username=f"{name}-author")
    assert UUID(author.id).version == 7

    entry = BackendEntry.objects.using(db).create(title=f"{name}-entry", author_id=author.id)
    assert BackendEntry.objects.using(db).get(id=entry.id).author.username == f"{name}-author"

    table = db.dialect.quote_identifier(BackendAuthor._meta.table_name)
    ph = db.dialect.placeholder
    raw_author = BackendAuthor.objects.using(db).raw(
        f"SELECT * FROM {table} WHERE id = {ph}", [author.id]
    ).first()
    assert raw_author.id == author.id

    with pytest.raises(ConfigurationError):
        BackendAuthor.objects.using(db).raw(f"SELECT * FROM {table}")

    detached = BackendAuthor(id=author.id, username=f"{name}-updated")
    detached.save(db)
    assert BackendAuthor.objects.using(db).count() == 1
    assert BackendAuthor.objects.using(db).get(id=author.id).username == f"{name}-updated"

    with pytest.raises(RuntimeError):
        with transaction.atomic(db):
            BackendAuthor.objects.using(db).create(username=f"{name}-rollback")
            raise RuntimeError("rollback")
    assert BackendAuthor.objects.using(db).filter(username=f"{name}-rollback").count() == 0

    db.close()

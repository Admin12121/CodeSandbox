import uuid

import pytest

import nexorm.uuid as uuid_module
from nexorm import Database, ForeignKey, IntegerField, Model, StringField, configure, transaction
from nexorm.dialects import MySQLDialect, PostgresDialect
from nexorm.exceptions import ConfigurationError
from nexorm.migrations.engine import MigrationEngine
from nexorm.migrations.state import model_state
from nexorm.migrations.operations import CreateTable


def create_table(model, db=None):
    db = db or model.objects.get_queryset().db
    dialect = db.dialect
    columns = [
        {
            "name": name,
            "sql": dialect.column_sql(field),
        }
        for name, field in model._meta.fields.items()
    ]
    for sql in CreateTable(model._meta.table_name, columns).to_sql(dialect):
        db.execute(sql)
    db.commit()


def test_model_create_and_query(tmp_path):
    configure(str(tmp_path / "test.sqlite3"))

    class User(Model):
        name = StringField(max_length=80)
        age = IntegerField(nullable=True)

    create_table(User)

    created = User.objects.create(name="Ada", age=36)
    found = User.objects.get(id=created.id)

    assert uuid.UUID(created.id).version == 7
    assert found.name == "Ada"
    assert found.age == 36
    assert User.objects.filter(name__contains="Ad").count() == 1

    with pytest.raises(ConfigurationError):
        User.objects.raw("SELECT * FROM users")

    raw_user = User.objects.raw("SELECT * FROM users WHERE id = ?", [created.id]).first()
    assert raw_user.id == created.id

    detached = User(id=created.id, name="Grace", age=37)
    detached.save()

    assert User.objects.count() == 1
    assert User.objects.get(id=created.id).name == "Grace"


def test_named_database_connection(tmp_path):
    primary = configure(str(tmp_path / "primary.sqlite3"))
    analytics = configure(str(tmp_path / "analytics.sqlite3"), alias="analytics")

    class Account(Model):
        username = StringField(max_length=80, unique=True)

    create_table(Account, primary)
    create_table(Account, analytics)

    Account.objects.create(username="primary")
    Account.objects.using("analytics").create(username="report")

    assert Account.objects.filter(username="primary").count() == 1
    assert Account.objects.using("analytics").filter(username="primary").count() == 0
    assert Account.objects.using("analytics").filter(username="report").count() == 1

    with transaction.atomic("analytics"):
        Account.objects.using("analytics").create(username="worker")

    assert Account.objects.using("analytics").filter(username="worker").exists()


def test_backend_url_configuration_without_connecting():
    postgres = Database("postgresql://app:secret@localhost:5432/appdb")
    mysql = Database("mysql://app:secret@localhost:3306/appdb")

    assert postgres.backend == "postgresql"
    assert postgres.database == "appdb"
    assert postgres.dialect.placeholder == "%s"
    assert mysql.backend == "mysql"
    assert mysql.database == "appdb"
    assert mysql.dialect.placeholder == "%s"
    assert MigrationEngine(db=postgres)._history_insert_sql().endswith("VALUES (%s)")
    assert MigrationEngine(db=mysql)._history_insert_sql().endswith("VALUES (%s)")


def test_portable_migration_state_renders_for_backend_dialects():
    class Ledger(Model):
        name = StringField(max_length=80)

    table = model_state()["tables"][Ledger._meta.table_name]
    operation = CreateTable(
        Ledger._meta.table_name,
        list(table["columns"].values()),
        table["foreign_keys"],
        table["indexes"],
    )

    postgres_sql = operation.to_sql(PostgresDialect())[0]
    mysql_sql = operation.to_sql(MySQLDialect())[0]

    assert '"id" UUID PRIMARY KEY' in postgres_sql
    assert '"name" VARCHAR(80) NOT NULL' in postgres_sql
    assert "`id` CHAR(36) PRIMARY KEY" in mysql_sql
    assert "`name` VARCHAR(80) NOT NULL" in mysql_sql


def test_foreign_key_on_delete_is_validated():
    with pytest.raises(ConfigurationError):

        class BadForeignKey(Model):
            author_id = ForeignKey("Ledger", on_delete="CASCADE; DROP TABLE users")


def test_uuid7_burst_does_not_drift_timestamp(monkeypatch):
    fixed_ms = 1_700_000_000_000
    monkeypatch.setattr(uuid_module.time, "time_ns", lambda: fixed_ms * 1_000_000)

    values = [uuid_module.uuid7() for _ in range(5000)]

    assert all(value.version == 7 for value in values)
    assert {value.int >> 80 for value in values} == {fixed_ms}


def test_relations_follow_the_queryset_database(tmp_path):
    primary = configure(str(tmp_path / "relations_primary.sqlite3"))
    analytics = configure(str(tmp_path / "relations_analytics.sqlite3"), alias="analytics")

    class Author(Model):
        username = StringField(max_length=80, unique=True)

    class Entry(Model):
        title = StringField(max_length=80)
        author_id = ForeignKey("Author", related_name="entries")

    create_table(Author, primary)
    create_table(Entry, primary)
    create_table(Author, analytics)
    create_table(Entry, analytics)

    Author.objects.create(username="primary")
    report_author = Author.objects.using("analytics").create(username="report")
    Entry.objects.using("analytics").create(title="analytics", author_id=report_author.id)

    entry = Entry.objects.using("analytics").get(title="analytics")

    assert entry.author.username == "report"
    assert report_author.entries.count() == 1

from nexorm.database import default_db
from nexorm.exceptions import IntegrityError, ValidationError
from nexorm.fields import ForeignKey


def validate_instance(instance, db=None):
    db = db or default_db
    errors = {}
    for name, field in instance._meta.fields.items():
        value = getattr(instance, name, None)
        if value is None and field.default is not None:
            value = field.get_default()
            setattr(instance, name, value)
        if not field.validate(value):
            errors[name] = "invalid value"
            continue
        if value is not None and field.unique and not _is_unique(instance, name, field, value, db):
            errors[name] = "must be unique"
        if value is not None and isinstance(field, ForeignKey) and not _foreign_key_exists(field, value, db):
            errors[name] = "referenced row does not exist"
    if errors:
        raise ValidationError(errors)
    return True


def _is_unique(instance, name, field, value, db):
    meta = instance._meta
    dialect = db.dialect
    pk = meta.primary_key
    sql = (
        f"SELECT {dialect.quote_identifier(pk.name)} FROM {dialect.quote_identifier(meta.table_name)} "
        f"WHERE {dialect.quote_identifier(name)} = {dialect.placeholder} LIMIT 2"
    )
    rows = db.fetchall(sql, [field.to_db(value)])
    current_pk = getattr(instance, pk.name, None)
    for row in rows:
        if current_pk is None or row[pk.name] != current_pk:
            return False
    return True


def _foreign_key_exists(field, value, db):
    target = field.target_model()
    meta = target._meta
    pk = meta.primary_key
    dialect = db.dialect
    sql = (
        f"SELECT 1 FROM {dialect.quote_identifier(meta.table_name)} "
        f"WHERE {dialect.quote_identifier(pk.name)} = {dialect.placeholder} LIMIT 1"
    )
    return db.fetchone(sql, [pk.to_db(value)]) is not None


def wrap_integrity_error(func):
    try:
        return func()
    except Exception as exc:
        message = str(exc).lower()
        if "constraint" in message or "unique" in message or "foreign key" in message:
            raise IntegrityError(str(exc)) from exc
        raise

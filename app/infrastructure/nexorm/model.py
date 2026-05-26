from nexorm.exceptions import ConfigurationError
from nexorm.fields import Field, UUIDField
from nexorm.manager import Manager
from nexorm.options import Options
from nexorm.registry import register_model
from nexorm.sql.crud import CRUDEngine
from nexorm.uuid import uuid7
from nexorm.validators import validate_instance, wrap_integrity_error


class ModelBase(type):
    def __new__(mcls, name, bases, attrs):
        meta_opts = attrs.pop("Meta", None)
        fields = {key: value for key, value in list(attrs.items()) if isinstance(value, Field)}
        for key in fields:
            attrs.pop(key)
        cls = super().__new__(mcls, name, bases, attrs)
        if name == "Model":
            return cls

        table_name = getattr(meta_opts, "table_name", None) if meta_opts else None
        cls._meta = Options(cls, table_name)
        inherited = {}
        for base in bases:
            if hasattr(base, "_meta"):
                inherited.update(base._meta.fields)
        for key, field in inherited.items():
            cls._meta.add_field(key, field)
        for key, field in fields.items():
            cls._meta.add_field(key, field)
        if cls._meta.primary_key is None:
            cls._meta.add_field("id", UUIDField(primary_key=True, default=uuid7))
        cls.objects = Manager()
        register_model(cls)
        return cls


class Model(metaclass=ModelBase):
    def __init__(self, **kwargs):
        provided_fields = set(kwargs)
        unknown = set(kwargs) - set(self._meta.fields)
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ConfigurationError(f"Unknown field(s) for {self.__class__.__name__}: {fields}")
        for name, field in self._meta.fields.items():
            value = kwargs.get(name, field.get_default())
            setattr(self, name, value)
        self._nexorm_pk_provided = self._meta.primary_key.name in provided_fields
        self._nexorm_persisted = False

    def validate(self, db=None):
        db = db or getattr(self, "_nexorm_db", None)
        return validate_instance(self, db)

    def save(self, db=None):
        db = db or getattr(self, "_nexorm_db", None)
        adding = not getattr(self, "_nexorm_persisted", False)
        self.validate(db)
        engine = CRUDEngine(db)
        if adding and self._nexorm_pk_provided and engine.exists(self):
            return wrap_integrity_error(lambda: engine.update(self))
        if adding:
            return wrap_integrity_error(lambda: engine.insert(self))
        return wrap_integrity_error(lambda: engine.update(self))

    def delete(self, db=None):
        db = db or getattr(self, "_nexorm_db", None)
        return wrap_integrity_error(lambda: CRUDEngine(db).delete(self))

    def update(self, db=None, **kwargs):
        db = db or getattr(self, "_nexorm_db", None)
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self.save(db)

    def to_dict(self):
        return {name: getattr(self, name, None) for name in self._meta.fields}

    @classmethod
    def from_row(cls, row, db=None):
        data = {}
        for name, field in cls._meta.fields.items():
            data[name] = field.from_db(row[name]) if name in row.keys() else None
        instance = cls(**data)
        if db is not None:
            instance._nexorm_db = db
        instance._nexorm_persisted = True
        return instance

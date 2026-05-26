import datetime as _dt
import uuid as _uuid
from decimal import Decimal
from nexorm.exceptions import ConfigurationError


VALID_ON_DELETE_ACTIONS = {"CASCADE", "RESTRICT", "SET NULL", "NO ACTION", "SET DEFAULT"}


def normalize_on_delete(value):
    action = str(value).replace("_", " ").upper()
    if action not in VALID_ON_DELETE_ACTIONS:
        valid = ", ".join(sorted(VALID_ON_DELETE_ACTIONS))
        raise ConfigurationError(
            f"Unsupported on_delete action: {value!r}. Expected one of: {valid}"
        )
    return action


class Field:
    type_name = "text"
    python_type = object

    def __init__(
        self,
        primary_key=False,
        nullable=False,
        unique=False,
        default=None,
        index=False,
        auto_increment=False,
        max_length=None,
    ):
        self.name = None
        self.model = None
        self.primary_key = primary_key
        self.nullable = nullable
        self.unique = unique
        self.default = default
        self.index = index
        self.auto_increment = auto_increment
        self.max_length = max_length

    def get_default(self):
        return self.default() if callable(self.default) else self.default

    def to_db(self, value):
        return value

    def from_db(self, value):
        return value

    def validate(self, value):
        if value is None:
            return self.nullable or self.primary_key or self.default is not None
        return isinstance(value, self.python_type) or self.python_type is object

    def deconstruct(self):
        return {
            "type": self.__class__.__name__,
            "primary_key": self.primary_key,
            "nullable": self.nullable,
            "unique": self.unique,
            "default": None if callable(self.default) else self.default,
            "index": self.index,
            "auto_increment": self.auto_increment,
            "max_length": self.max_length,
        }


class IntegerField(Field):
    type_name = "integer"
    python_type = int


class StringField(Field):
    type_name = "string"
    python_type = str

    def __init__(self, max_length=255, **kwargs):
        super().__init__(max_length=max_length, **kwargs)

    def validate(self, value):
        if not super().validate(value):
            return False
        return value is None or len(value) <= self.max_length


class TextField(Field):
    type_name = "text"
    python_type = str


class UUIDField(Field):
    type_name = "uuid"

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 36)
        super().__init__(**kwargs)

    def get_default(self):
        return self.from_db(super().get_default())

    def to_db(self, value):
        return None if value is None else str(value)

    def from_db(self, value):
        return None if value is None else str(value)

    def validate(self, value):
        if value is None:
            return super().validate(value)
        try:
            _uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return False
        return True


class BooleanField(Field):
    type_name = "boolean"
    python_type = bool

    def to_db(self, value):
        return None if value is None else int(value)

    def from_db(self, value):
        return None if value is None else bool(value)


class DateTimeField(Field):
    type_name = "datetime"
    python_type = _dt.datetime

    def to_db(self, value):
        return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value

    def from_db(self, value):
        if isinstance(value, str):
            return _dt.datetime.fromisoformat(value)
        return value


class FloatField(Field):
    type_name = "float"
    python_type = float


class DecimalField(Field):
    type_name = "decimal"
    python_type = Decimal

    def __init__(self, max_digits=10, decimal_places=2, **kwargs):
        super().__init__(**kwargs)
        self.max_digits = max_digits
        self.decimal_places = decimal_places

    def to_db(self, value):
        return None if value is None else str(value)

    def from_db(self, value):
        return None if value is None else Decimal(str(value))

    def validate(self, value):
        if not super().validate(value):
            return False
        if value is None:
            return True
        normalized = value.as_tuple()
        digits = len(normalized.digits)
        decimals = abs(normalized.exponent) if normalized.exponent < 0 else 0
        return digits <= self.max_digits and decimals <= self.decimal_places

    def deconstruct(self):
        data = super().deconstruct()
        data.update({"max_digits": self.max_digits, "decimal_places": self.decimal_places})
        return data


class ForeignKey(IntegerField):
    def __init__(self, to, on_delete="CASCADE", related_name=None, **kwargs):
        kwargs.setdefault("index", True)
        super().__init__(**kwargs)
        self.to = to
        self.on_delete = normalize_on_delete(on_delete)
        self.related_name = related_name

    def target_model(self):
        if isinstance(self.to, str):
            from nexorm.registry import get_model

            try:
                return get_model(self.to)
            except KeyError as exc:
                raise ConfigurationError(f"Unknown foreign key target model: {self.to}") from exc
        return self.to() if callable(self.to) and not isinstance(self.to, type) else self.to

    def target_table_field(self):
        model = self.target_model()
        return model._meta.table_name, model._meta.primary_key.name

    def target_field(self):
        return self.target_model()._meta.primary_key

    def validate(self, value):
        if value is None:
            return Field.validate(self, value)
        try:
            return self.target_field().validate(value)
        except ConfigurationError:
            return super().validate(value)

    def to_db(self, value):
        if value is None:
            return None
        try:
            return self.target_field().to_db(value)
        except ConfigurationError:
            return super().to_db(value)

    def from_db(self, value):
        if value is None:
            return None
        try:
            return self.target_field().from_db(value)
        except ConfigurationError:
            return super().from_db(value)

    def deconstruct(self):
        data = super().deconstruct()
        target = self.to if isinstance(self.to, str) else self.target_model().__name__
        data.update({"to": target, "on_delete": self.on_delete, "related_name": self.related_name})
        try:
            data["target_field"] = self.target_field().deconstruct()
        except ConfigurationError:
            pass
        return data

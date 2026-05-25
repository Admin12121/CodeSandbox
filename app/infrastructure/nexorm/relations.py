from nexorm.fields import ForeignKey


class ForeignKeyDescriptor:
    def __init__(self, field):
        self.field = field
        self.storage_name = field.name
        self.cache_name = f"_nexorm_cache_{field.name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        value = instance.__dict__.get(self.storage_name)
        if value is None:
            return None
        if self.cache_name not in instance.__dict__:
            target = self.field.target_model()
            instance.__dict__[self.cache_name] = target.objects.get(**{target._meta.primary_key.name: value})
        return instance.__dict__[self.cache_name]

    def __set__(self, instance, value):
        target = self.field.target_model()
        if isinstance(value, target):
            instance.__dict__[self.cache_name] = value
            value = getattr(value, target._meta.primary_key.name)
        else:
            instance.__dict__.pop(self.cache_name, None)
        instance.__dict__[self.storage_name] = value


class ReverseRelation:
    def __init__(self, from_model, field):
        self.from_model = from_model
        self.field = field

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.from_model.objects.filter(**{self.field.name: getattr(instance, owner._meta.primary_key.name)})


def install_relations(model):
    for field in model._meta.foreign_keys:
        if isinstance(field, ForeignKey):
            relation_name = field.name.replace("_id", "")
            if not hasattr(model, relation_name):
                setattr(model, relation_name, ForeignKeyDescriptor(field))
            related = field.related_name or model._meta.table_name
            target = field.target_model()
            if not hasattr(target, related):
                setattr(target, related, ReverseRelation(model, field))


def install_all_relations():
    from nexorm.exceptions import ConfigurationError
    from nexorm.registry import get_models

    for model in get_models():
        try:
            install_relations(model)
        except ConfigurationError:
            continue

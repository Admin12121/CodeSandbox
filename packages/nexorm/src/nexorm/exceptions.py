class NexORMError(Exception):
    """Base exception for NexORM."""


class ValidationError(NexORMError):
    pass


class IntegrityError(NexORMError):
    pass


class DoesNotExist(NexORMError):
    pass


class MultipleObjectsReturned(NexORMError):
    pass


class ConfigurationError(NexORMError):
    pass

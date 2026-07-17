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


class PoolTimeoutError(ConfigurationError):
    """Raised when no pooled connection becomes available within the
    configured pool_timeout — the pool is exhausted (pool_size concurrent
    connections all checked out) rather than the database being down."""

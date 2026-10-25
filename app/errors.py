"""调用方可分别处理的存储与业务错误。"""


class StorageError(RuntimeError):
    pass


class InitializationRequired(StorageError):
    pass


class SchemaError(StorageError):
    pass


class ValidationError(StorageError, ValueError):
    pass


class NotFound(StorageError):
    pass


class VersionConflict(StorageError):
    pass


class InvalidState(StorageError):
    pass

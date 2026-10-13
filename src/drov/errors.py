"""Pre-run/control exceptions. Recorded execution errors use RunError."""


class DrovError(Exception):
    code = "drov_error"


class ConfigurationError(DrovError, ValueError):
    code = "configuration_error"


class UnsupportedSchema(ConfigurationError):
    code = "unsupported_schema"


class ModelProtocolError(DrovError):
    code = "model_protocol_error"


class PersistenceUnavailable(DrovError):
    code = "persistence_unavailable"


class StorageInUse(DrovError):
    code = "storage_in_use"


class Conflict(DrovError):
    code = "conflict"


class InvalidCursor(DrovError, ValueError):
    code = "invalid_cursor"


class CursorExpired(DrovError):
    code = "cursor_expired"


class PermissionDenied(DrovError):
    code = "permission_denied"


class CapacityExceeded(DrovError):
    code = "capacity_exceeded"


class ShutdownIncomplete(DrovError):
    code = "shutdown_incomplete"

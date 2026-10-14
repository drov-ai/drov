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


class ArtifactUnavailable(DrovError):
    code = "artifact_unavailable"


class StorageInUse(DrovError):
    code = "storage_in_use"


class Conflict(DrovError):
    code = "conflict"


class InteractionExpired(Conflict):
    code = "interaction_expired"


class InteractionConflict(Conflict):
    code = "interaction_conflict"


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


class SessionBusy(Conflict):
    code = "session_busy"


class RecordNotFound(DrovError):
    code = "not_found"


class IncompatibleStorage(ConfigurationError):
    code = "incompatible_storage"


class TransientToolError(DrovError):
    """Application-declared transient read failure; never enables write retries."""

    code = "transient_tool_error"


class TransientModelError(DrovError):
    """Adapter-declared transient failure; permits only bounded model retries."""

    code = "transient_model_error"

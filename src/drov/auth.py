"""Host identity/authorization boundaries and the conservative owner default."""

from typing import Literal, Protocol, TypeAlias

from drov._json import StrictJson
from drov.types import Identifier, RunContext, ScopedIdentifier, WireModel

Action: TypeAlias = Literal[
    "read", "view_content", "start", "respond", "cancel", "recover", "verify", "execute_tool"
]


class Resource(WireModel):
    owner: RunContext
    run_id: Identifier | None = None
    session_id: Identifier | None = None


class Authorizer(Protocol):
    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool: ...


class ToolResource(Resource):
    tool_name: ScopedIdentifier
    operation_id: Identifier
    arguments: dict[str, StrictJson]


class OwnerAuthorizer:
    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        return actor == resource.owner


class AuthAdapter(Protocol):
    async def authenticate(self, credential: str) -> RunContext: ...

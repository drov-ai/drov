"""Host identity/authorization interfaces. No permission engine is implemented here."""

from typing import Literal, Protocol, TypeAlias

from drov.types import Identifier, RunContext, WireModel

Action: TypeAlias = Literal[
    "read", "view_content", "start", "respond", "cancel", "recover", "verify"
]


class Resource(WireModel):
    owner: RunContext
    run_id: Identifier | None = None
    session_id: Identifier | None = None


class Authorizer(Protocol):
    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool: ...


class AuthAdapter(Protocol):
    async def authenticate(self, credential: str) -> RunContext: ...

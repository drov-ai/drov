"""Ordered tool permissions; model fields cannot supply a grant."""

from collections.abc import Iterable
from typing import Annotated, Any, Literal, cast

from pydantic import BeforeValidator, Field

from drov.auth import Authorizer, ToolResource
from drov.errors import ConfigurationError
from drov.tools.schema import ToolDefinition
from drov.types import RunContext, ScopedIdentifier, WireModel


def _rules(value: object) -> object:
    if type(value) not in (set, frozenset, list):
        raise ValueError("Permission rules must be a collection of names")
    return frozenset(cast(Iterable[object], value))


Rules = Annotated[frozenset[ScopedIdentifier], BeforeValidator(_rules)]


class PermissionDecision(WireModel):
    decision: Literal["deny", "ask", "allow"]
    reason: Literal[
        "scope",
        "authorization",
        "authorization_error",
        "deny",
        "approval",
        "ask",
        "allow",
        "read",
        "default",
    ]


class PermissionPolicy(WireModel):
    deny: Rules = Field(default_factory=frozenset)
    ask: Rules = Field(default_factory=frozenset)
    allow: Rules = Field(default_factory=frozenset)

    def __init__(
        self,
        *,
        deny: set[str] | frozenset[str] | list[str] = frozenset(),
        ask: set[str] | frozenset[str] | list[str] = frozenset(),
        allow: set[str] | frozenset[str] | list[str] = frozenset(),
        **data: Any,
    ) -> None:
        values: dict[str, Any] = {"deny": deny, "ask": ask, "allow": allow} | data
        super().__init__(**values)

    async def decide(
        self,
        tool: ToolDefinition,
        *,
        actor: RunContext,
        resource: ToolResource,
        authorizer: Authorizer,
    ) -> PermissionDecision:
        if resource.tool_name != tool.name:
            raise ConfigurationError("Tool authorization resource has a different name")
        if actor != resource.owner:
            return PermissionDecision(decision="deny", reason="scope")
        granted = await authorizer.authorize(actor, "execute_tool", resource)
        if type(granted) is not bool:
            raise ConfigurationError("Authorizer must return a bool")
        if not granted:
            return PermissionDecision(decision="deny", reason="authorization")
        if tool.name in self.deny:
            return PermissionDecision(decision="deny", reason="deny")
        if tool.approval == "required":
            return PermissionDecision(decision="ask", reason="approval")
        if tool.name in self.ask:
            return PermissionDecision(decision="ask", reason="ask")
        if tool.name in self.allow:
            return PermissionDecision(decision="allow", reason="allow")
        if tool.effect == "read":
            return PermissionDecision(decision="allow", reason="read")
        return PermissionDecision(decision="ask", reason="default")

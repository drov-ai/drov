import pytest

from drov import OwnerAuthorizer, PermissionPolicy, tool
from drov.auth import Authorizer, ToolResource
from drov.errors import ConfigurationError
from drov.types import RunContext


@tool(name="search", effect="read")
def search(query: str) -> str:
    raise AssertionError("Policy evaluation cannot execute a tool")


class DenyAuthorizer:
    async def authorize(self, actor: RunContext, action: object, resource: object) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect,approval,deny,ask,allow,granted,expected,reason",
    [
        ("read", "policy", False, False, False, True, "allow", "read"),
        ("unknown", "policy", False, False, False, True, "ask", "default"),
        ("write", "policy", False, False, False, True, "ask", "default"),
        ("read", "policy", True, True, True, True, "deny", "deny"),
        ("write", "required", False, False, True, True, "ask", "approval"),
        ("read", "policy", False, True, True, True, "ask", "ask"),
        ("write", "policy", False, False, True, True, "allow", "allow"),
        ("unknown", "policy", False, False, True, True, "allow", "allow"),
        ("read", "policy", False, False, True, False, "deny", "authorization"),
    ],
)
async def test_order(
    effect: str,
    approval: str,
    deny: bool,
    ask: bool,
    allow: bool,
    granted: bool,
    expected: str,
    reason: str,
) -> None:
    definition = search.__class__(**(search.__dict__ | {"effect": effect, "approval": approval}))
    actor = RunContext(tenant_id="t", user_id="alice")
    resource = ToolResource(
        owner=actor, tool_name="search", operation_id="op", arguments={"query": "q"}
    )
    policy = PermissionPolicy(
        deny={"search"} if deny else set(),
        ask={"search"} if ask else set(),
        allow={"search"} if allow else set(),
    )
    authorizer: Authorizer = OwnerAuthorizer() if granted else DenyAuthorizer()
    decision = await policy.decide(
        definition, actor=actor, resource=resource, authorizer=authorizer
    )
    assert (decision.decision, decision.reason) == (expected, reason)


@pytest.mark.asyncio
async def test_scope_veto_and_resource_name() -> None:
    actor = RunContext(tenant_id="t", user_id="alice")
    other = RunContext(tenant_id="t", user_id="bob")
    resource = ToolResource(owner=other, tool_name="search", operation_id="op", arguments={})
    result = await PermissionPolicy(allow={"search"}).decide(
        search, actor=actor, resource=resource, authorizer=OwnerAuthorizer()
    )
    assert result.reason == "scope" and result.decision == "deny"
    with pytest.raises(ConfigurationError):
        await PermissionPolicy().decide(
            search,
            actor=actor,
            resource=resource.model_copy(update={"tool_name": "other"}),
            authorizer=OwnerAuthorizer(),
        )
    for action in (
        "read",
        "view_content",
        "start",
        "respond",
        "cancel",
        "recover",
        "verify",
        "execute_tool",
    ):
        assert not await OwnerAuthorizer().authorize(actor, action, resource)


def test_policy_json_and_immutable_rules() -> None:
    policy = PermissionPolicy(deny={"a"}, ask={"b"}, allow={"c"})
    assert PermissionPolicy.from_json(policy.model_dump_json()) == policy
    assert isinstance(policy.allow, frozenset)
    for bad in ("all", {"*": True}, [""], [1]):
        with pytest.raises((ValueError, TypeError)):
            PermissionPolicy(allow=bad)  # type: ignore[arg-type]

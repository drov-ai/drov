import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from drov._json import canonical_json, load_json
from drov.config import ContextPolicy, Limits, ModelCapabilities, validate_input
from drov.types import (
    ApprovalDecision,
    ApprovalRequest,
    Cancelled,
    Completed,
    ContextManifest,
    ContextSource,
    DurableEvent,
    Failed,
    InputResponse,
    InteractionSummary,
    ReconciliationDecision,
    ResponseReceipt,
    RunContext,
    RunError,
    RunInspection,
    RunRef,
    RunSnapshot,
    StreamError,
    Suspended,
    TokenDelta,
    TransientEvent,
    UnknownDurableEvent,
    Usage,
    outcome_adapter,
    parse_event,
    parse_outcome,
    parse_response,
)

NOW = datetime(2026, 10, 13, tzinfo=UTC)
LATER = datetime(2026, 10, 14, tzinfo=UTC)


def approval() -> ApprovalRequest:
    return ApprovalRequest(
        request_id="req",
        run_id="run",
        operation_id="op",
        created_at=NOW,
        expires_at=LATER,
        prompt="Approve?",
        tool_name="update",
        arguments_summary={"item_id": "a"},
    )


def snapshot(**changes: Any) -> RunSnapshot:
    values = dict(
        run_id="run",
        state_version=1,
        status="running",
        agent_name="agent",
        revision="1",
        session_id="session",
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return RunSnapshot.model_validate(values)


@pytest.mark.parametrize(
    "outcome",
    [
        Completed[str](run_id="run", state_version=1, output="ok"),
        Suspended(run_id="run", state_version=1, reason="approval", pending=approval()),
        Suspended(run_id="run", state_version=1, reason="recovery", pending=None),
        Failed(
            run_id="run",
            state_version=1,
            error=RunError(code="failure", message="safe", stage="tool"),
        ),
        Cancelled(run_id="run", state_version=1),
    ],
)
def test_outcome_roundtrip_and_discriminator(outcome: Any) -> None:
    restored = parse_outcome(outcome.model_dump_json())
    assert restored.model_dump() == outcome.model_dump()
    if outcome.status != "completed":
        assert not hasattr(restored, "output")
        value = outcome.model_dump(mode="json") | {"output": "fake success"}
        with pytest.raises(ValidationError):
            parse_outcome(canonical_json(value))


def test_runtime_bound_outcome_adapter() -> None:
    adapter = outcome_adapter(str)
    value = adapter.validate_python(
        {"status": "completed", "run_id": "r", "state_version": 1, "output": "ok"}
    )
    assert isinstance(value, Completed)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"status": "completed", "run_id": "r", "state_version": 1, "output": 17}
        )


@pytest.mark.parametrize("value", [0, -1, 1.5, "1", True, None])
def test_versions_reject_invalid_numbers(value: object) -> None:
    with pytest.raises(ValidationError):
        RunRef.model_validate({"run_id": "r", "state_version": value})


@pytest.mark.parametrize("value", ["", "a\n", "a\x00", "a\x7f", "a\x85", "x" * 129])
def test_identity_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        RunContext(tenant_id=value, user_id="user")


@pytest.mark.parametrize(
    "value",
    ["2026-10-13T00:00:00", "2026-10-13T08:00:00+08:00", "2026-10-13", 1, datetime(2026, 10, 13)],
)
def test_time_requires_explicit_utc(value: object) -> None:
    with pytest.raises(ValidationError):
        ResponseReceipt.model_validate(
            {
                "run_id": "r",
                "state_version": 1,
                "request_id": "q",
                "response_id": "a",
                "accepted_at": value,
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "max_model_steps",
        "max_input_bytes",
        "active_slots",
        "run_active_seconds",
        "shutdown_grace_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, "1", None, True, float("inf"), float("nan")])
def test_positive_limits(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Limits.model_validate({field: value})


def test_retry_and_administrator_limits() -> None:
    assert Limits(model_retries=0, read_tool_retries=0).model_retries == 0
    for value in (-1, "0", True):
        with pytest.raises(ValidationError):
            Limits.model_validate({"model_retries": value})
    for value in (1, False):
        with pytest.raises(ValidationError):
            Limits.model_validate({"write_tool_retries": value})
    ceiling = Limits(max_input_tokens=1000)
    assert Limits(max_input_tokens=500).require_within(ceiling).max_input_tokens == 500
    for input_budget in (None, 1001):
        with pytest.raises(ValueError):
            Limits(max_input_tokens=input_budget).require_within(ceiling)
    with pytest.raises(ValidationError):
        Limits(page_size=100, max_page_size=50)


def test_unknown_usage_and_capabilities() -> None:
    assert Usage().input_tokens is None
    assert Usage().cost_amount is None
    assert ModelCapabilities().text is False
    assert ModelCapabilities().context_window is None
    for value in (-1, True, "0"):
        with pytest.raises(ValidationError):
            Usage.model_validate({"input_tokens": value})
    for value in ("NaN", "-1", "1e3", "01"):
        with pytest.raises(ValidationError):
            Usage(cost_amount=value, currency="USD")
    with pytest.raises(ValidationError):
        Usage(cost_amount="1.00")
    with pytest.raises(ValidationError):
        ContextPolicy(memory_fraction=float("nan"))


@pytest.mark.parametrize(
    "data", ['{"a":1,"a":2}', '{"a":{"b":1,"b":2}}', "NaN", "Infinity", "-Infinity", "1e999"]
)
def test_json_rejects_ambiguous_or_nonfinite_values(data: str) -> None:
    with pytest.raises(ValueError):
        load_json(data)


def test_input_bytes_and_json_boundaries() -> None:
    assert validate_input("你", Limits(max_input_bytes=3)) == "你"
    for value in ("你好", "", 12):
        with pytest.raises(ValueError):
            validate_input(value, Limits(max_input_bytes=3))
    with pytest.raises(ValueError):
        load_json('"你"', max_bytes=4)
    for json_value in ({1: "a"}, {"a": float("nan")}, (1, 2), NOW):
        with pytest.raises(ValueError):
            InputResponse.model_validate({"value": json_value})


def test_response_presence_and_injection() -> None:
    assert isinstance(
        parse_response(ApprovalDecision(approved=True).model_dump_json()), ApprovalDecision
    )
    absent = ReconciliationDecision(resolution="applied", evidence="effect verified")
    null = ReconciliationDecision(resolution="applied", evidence="effect verified", result=None)
    assert not parse_response(absent.model_dump_json()).has_result  # type: ignore[union-attr]
    assert parse_response(null.model_dump_json()).has_result  # type: ignore[union-attr]
    assert "result" not in json.loads(absent.model_dump_json())
    assert "result" in json.loads(null.model_dump_json())
    for resolution in ("not_applied", "unresolved"):
        with pytest.raises(ValidationError):
            ReconciliationDecision.model_validate(
                {"resolution": resolution, "evidence": "verified", "result": None}
            )
    with pytest.raises(ValidationError):
        ApprovalDecision.model_validate({"approved": True, "arguments": {"id": "new"}})
    with pytest.raises(ValidationError):
        RunContext.model_validate({"tenant_id": "t", "user_id": "u", "deps": {}})


def test_snapshot_body_is_an_explicit_projection() -> None:
    full = approval()
    summary = InteractionSummary.model_validate(
        full.model_dump(exclude={"prompt", "tool_name", "arguments_summary"})
    )
    meta = snapshot(status="suspended", reason="approval", pending=summary)
    assert meta.outcome is None
    assert "item_id" not in meta.model_dump_json()
    with pytest.raises(ValidationError):
        snapshot(status="suspended", reason="approval", pending=full)
    with pytest.raises(ValidationError):
        snapshot(
            status="completed",
            outcome=Completed[str](run_id="run", state_version=1, output="secret"),
        )
    body = snapshot(status="suspended", reason="approval", pending=full, content_visible=True)
    assert RunSnapshot.from_json(body.model_dump_json()) == body
    for changes in (
        {"status": "suspended"},
        {"status": "running", "pending": summary},
        {"status": "suspended", "reason": "input", "pending": summary},
    ):
        with pytest.raises(ValidationError):
            snapshot(**changes)


def test_suspension_request_belongs_to_run() -> None:
    with pytest.raises(ValidationError):
        Suspended(run_id="other", state_version=1, reason="approval", pending=approval())
    with pytest.raises(ValidationError):
        Suspended(run_id="run", state_version=1, reason="input", pending=approval())


def test_events_future_retention_and_transient_cursor() -> None:
    event = DurableEvent(
        run_id="run", state_version=1, seq=1, type="run.running", occurred_at=NOW, data={}
    )
    assert parse_event(event.model_dump_json()) == event
    future = event.model_dump(mode="json") | {"type": "future.custom", "data": {"new": [1, None]}}
    assert isinstance(parse_event(canonical_json(future)), UnknownDurableEvent)
    for changes in ({"durable": 1}, {"schema_version": True}, {"seq": 0}):
        with pytest.raises(ValidationError):
            parse_event(canonical_json(event.model_dump(mode="json") | changes))
    token = TokenDelta(run_id="run", data="hi")
    assert TypeAdapter(TransientEvent).validate_python(token.model_dump()) == token
    with pytest.raises(ValidationError):
        TokenDelta.model_validate(token.model_dump() | {"seq": 1})
    with pytest.raises(ValidationError):
        StreamError.model_validate({"run_id": "run", "data": {"raw_exception": "secret"}})
    page = RunInspection(snapshot=snapshot(), events=[event], next_cursor="run:1")
    assert RunInspection.from_json(page.model_dump_json()) == page
    page_changes: dict[str, Any]
    for page_changes in (
        {"next_cursor": "run:2"},
        {"events": [event, event]},
        {"events": [], "next_cursor": None, "has_more": True},
    ):
        with pytest.raises(ValidationError):
            RunInspection.model_validate(page.model_dump() | page_changes)


def test_context_summary_and_provider_body_guard() -> None:
    source = ContextSource(
        id="source",
        version="1",
        kind="memory",
        included=True,
        reason="selected",
        estimated_tokens=1,
        content="secret",
    )
    values = dict(
        model_call_id="call",
        run_id="run",
        agent_name="agent",
        revision="1",
        input_ref="ref",
        input_hash="a" * 64,
        budget=100,
        estimator="utf8-v1",
        sources=[source],
        retrieval_status="included",
    )
    with pytest.raises(ValidationError):
        ContextManifest.model_validate(values)
    manifest = ContextManifest.model_validate(values | {"content_visible": True})
    assert ContextManifest.from_json(manifest.model_dump_json()) == manifest
    values["sources"] = [source.model_copy(update={"kind": "provider"})]
    with pytest.raises(ValidationError):
        ContextManifest.model_validate(values | {"content_visible": True})

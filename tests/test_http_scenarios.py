"""Three reference application shapes use equal SDK, stream and HTTP execution."""

from typing import Any

import pytest

from drov import ApprovalDecision, DrovApp, InputResponse, PermissionPolicy, Runtime
from drov.types import Completed
from examples.http_offline import fixtures
from tests.test_http import ALICE, Auth, client

pytest.importorskip("fastapi")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream", "http"])
@pytest.mark.parametrize(
    "name,expected",
    [("research", "cache-a vs cache-b"), ("assistant", "saved"), ("operations", "done")],
)
async def test_reference_scenario_entry_equivalence(entry: str, name: str, expected: str) -> None:
    agents, writes = fixtures()
    a = agents[name]
    async with Runtime(
        agents=list(agents.values()), policy=PermissionPolicy(allow={"memory.remember"})
    ) as runtime:
        app = DrovApp(runtime=runtime, auth=Auth())
        async with client(app) as connection:
            if entry == "sdk":
                outcome = await runtime.run(a, "synthetic", session_id="s", context=ALICE)
            elif entry == "stream":
                async with runtime.stream(a, "synthetic", session_id="s", context=ALICE) as stream:
                    async for _ in stream:
                        pass
                    outcome = await stream.result()
            else:
                accepted = await connection.post(
                    "/v1/runs",
                    json=dict(agent_name=name, revision="1", session_id="s", input="synthetic"),
                )
                assert accepted.status_code == 202
                outcome = await runtime.wait(accepted.json()["run_id"], context=ALICE)
            pauses = []
            while outcome.status == "suspended":
                assert outcome.pending and writes == []
                pauses.append(outcome.reason)
                response: Any = (
                    InputResponse(value="chosen")
                    if outcome.reason == "input"
                    else ApprovalDecision(approved=True)
                )
                if entry == "http":
                    body = {
                        "request_id": outcome.pending.request_id,
                        "response": response.model_dump(mode="json"),
                    }
                    receipt = await connection.post(
                        f"/v1/runs/{outcome.run_id}/responses", json=body
                    )
                    assert receipt.status_code == 202
                    assert (
                        await connection.post(f"/v1/runs/{outcome.run_id}/responses", json=body)
                    ).json() == receipt.json()
                    outcome = await runtime.wait(outcome.run_id, context=ALICE)
                else:
                    assert outcome.pending is not None
                    outcome = await runtime.resume(
                        outcome.run_id,
                        context=ALICE,
                        request_id=outcome.pending.request_id,
                        response=response,
                    )
            assert isinstance(outcome, Completed) and outcome.output == expected
            page = await connection.get(f"/v1/runs/{outcome.run_id}?include_content=true")
            assert (
                page.status_code == 200 and page.json()["snapshot"]["outcome"]["output"] == expected
            )
            assert [item["status"] for item in page.json()["snapshot"]["operations"]] == (
                ["succeeded", "succeeded"] if name == "operations" else ["succeeded"]
            )
            if name == "operations":
                assert pauses == ["input", "approval"] and writes == [7]
            elif name == "assistant":
                assert "memory.changed" in [event["type"] for event in page.json()["events"]]
            else:
                assert pauses == [] and writes == []
            reconnect = await connection.get(f"/v1/runs/{outcome.run_id}/events")
            assert "event: run.completed" in reconnect.text

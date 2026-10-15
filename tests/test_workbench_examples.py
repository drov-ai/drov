"""A reusable offline example must consume the current turn's actual tool result."""

import json

import pytest

from drov.models.protocol import ModelComplete, ModelRequest
from examples.workbench_offline import FileDemoModel


@pytest.mark.asyncio
async def test_csv_demo_uses_current_result_when_session_has_prior_tool_history() -> None:
    old_id, new_id = "art_" + "1" * 32, "art_" + "2" * 32

    def result(artifact_id: str) -> dict[str, object]:
        return {
            "status": "succeeded",
            "result": {
                "execution_key": "3" * 32,
                "exit_code": 0,
                "termination": "exited",
                "stdout": "60\n",
                "stderr": "",
                "artifact_status": "complete",
                "artifacts": [
                    {
                        "artifact_id": artifact_id,
                        "sha256": "4" * 64,
                        "size_bytes": 25,
                        "media_type": "text/csv",
                    }
                ],
            },
        }

    request = ModelRequest.from_json(
        json.dumps(
            {
                "model_call_id": "current-call",
                "messages": [
                    {"role": "user", "content": "previous CSV"},
                    {"role": "tool", "call_id": "old", "content": result(old_id)},
                    {"role": "user", "content": "current CSV"},
                    {"role": "tool", "call_id": "new", "content": result(new_id)},
                    {
                        "role": "user",
                        "content": "Granted artifact descriptors (file data): []",
                    },
                ],
            }
        )
    )
    before = request.model_dump_json()
    values = [value async for value in FileDemoModel().generate(request)]
    assert len(values) == 1 and isinstance(values[0], ModelComplete)
    assert new_id in (values[0].response.message or "")
    assert old_id not in (values[0].response.message or "")
    assert request.model_dump_json() == before

"""S-01-C: inspect a marked excerpt and its durable source without claiming a full read."""

import argparse
import asyncio
from dataclasses import dataclass

from drov import Agent, Limits, RunContext, Runtime, tool
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelComplete, ModelRequest, ModelResponse, ToolCall
from drov.types import Completed


@dataclass
class PreviewReport:
    source_id: str
    full_document_read: bool
    conclusion: str


async def inspect_preview(*, streamed: bool = False) -> PreviewReport:
    original = {
        "source_id": "synthetic-guide",
        "text": "合成资料：缓存适合重复读取；这里只演示来源检查。" * 2000 + "FULL_DOCUMENT_END",
    }

    @tool(name="read_guide", effect="read")
    def read_guide() -> dict[str, str]:
        return original

    def check_preview(request: ModelRequest) -> None:
        message = request.messages[-1]
        assert message.role == "tool" and message.call_id == "guide-call"
        assert isinstance(message.content, dict)
        preview = message.content["result"]
        assert isinstance(preview, dict) and preview["type"] == "drov.tool_preview"
        assert (
            isinstance(preview["text"], str) and '"source_id":"synthetic-guide"' in preview["text"]
        )
        assert preview["truncated"] is True and "FULL_DOCUMENT_END" not in preview["text"]
        assert preview["source_ref"] and preview["source_version"] and preview["sha256"]

    answer = ModelResponse(
        finish_reason="completed",
        message='{"source_id":"synthetic-guide","full_document_read":false,'
        '"conclusion":"仅依据标记预览；未读取完整资料。"}',
    )
    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(call_id="guide-call", name="read_guide", arguments={}),
                ],
            ),
            ScriptedStep(events=(ModelComplete(response=answer),), check=check_preview),
        ]
    )
    agent = Agent(
        name="preview-reader",
        revision="1",
        model=model,
        tools=[read_guide],
        output_type=PreviewReport,
        instructions="保持来源引用，明确标记预览，不声称已读全文。",
        limits=Limits(max_input_tokens=4000, tool_preview_bytes=2048),
    )
    caller = RunContext(tenant_id="offline", user_id="reader")
    async with Runtime(agents=[agent]) as runtime:
        if streamed:
            async with runtime.stream(
                agent, "查看合成资料的预览", session_id="preview", context=caller
            ) as stream:
                async for event in stream:
                    print(event.type)
                outcome = await stream.result()
        else:
            outcome = await runtime.run(
                agent, "查看合成资料的预览", session_id="preview", context=caller
            )
        assert isinstance(outcome, Completed) and isinstance(outcome.output, PreviewReport)
        assert (
            outcome.output.source_id == original["source_id"]
            and not outcome.output.full_document_read
        )
        page = await runtime.inspect(outcome.run_id, context=caller)
        assert len(page.context_manifests) == 2
        summary = page.context_manifests[-1]
        metadata = await runtime.get_context(outcome.run_id, summary.model_call_id, context=caller)
        assert all(source.content is None for source in metadata.sources)
        manifest = await runtime.get_context(
            outcome.run_id, summary.model_call_id, context=caller, include_content=True
        )
        source = next(source for source in manifest.sources if source.kind == "tool")
        assert source.included and source.transform == "preview"
        assert isinstance(source.content, dict)
        envelope = source.content["content"]
        assert isinstance(envelope, dict) and isinstance(envelope["result"], dict)
        assert envelope["result"]["source_ref"] == source.id
        assert envelope["result"]["source_version"] == source.version
        assert manifest.estimated_tokens <= manifest.budget
        print(
            f"context: estimator={manifest.estimator}, estimate={manifest.estimated_tokens}, "
            f"budget={manifest.budget}, transform={source.transform}"
        )
        return outcome.output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(inspect_preview(streamed=args.stream)))

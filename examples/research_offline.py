"""S-01-A: real local retrieval + scripted model + typed report, without credentials."""

import argparse
import asyncio
from dataclasses import dataclass

from drov import Agent, RunContext, Runtime, tool
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelComplete, ModelRequest, ModelResponse, ToolCall
from drov.types import Completed


@dataclass
class Source:
    id: str
    text: str


@dataclass
class Report:
    conclusion: str
    source_ids: list[str]


CORPUS = (
    Source("cache-a", "方案 A 是进程内缓存：读取延迟低；多个进程各持有一份；重启会丢失。"),
    Source("cache-b", "方案 B 是独立共享缓存：多个进程可共享；需要网络和独立服务。"),
    Source("unrelated", "本段讨论日志轮换，与缓存选择无关。"),
)


async def research(*, streamed: bool = False) -> Report:
    retrieved: list[Source] = []

    @tool(name="search_docs", effect="read")
    def search_docs(query: str) -> list[Source]:
        if "缓存" not in query:
            return []
        retrieved.extend(source for source in CORPUS if source.id.startswith("cache-"))
        return list(retrieved)

    def check_results(request: ModelRequest) -> None:
        tool_message = request.messages[-1]
        assert tool_message.role == "tool" and tool_message.call_id == "search-1"
        assert isinstance(tool_message.content, dict)
        assert tool_message.content["status"] == "succeeded"
        assert tool_message.content["result"] == [
            {"id": source.id, "text": source.text} for source in retrieved
        ]

    answer = ModelResponse(
        message='{"conclusion":"A 适合单进程的低延迟读取；'
        'B 适合多个进程共享，但增加网络和服务依赖。",'
        '"source_ids":["cache-a","cache-b"]}',
        finish_reason="completed",
    )
    # Answers are predefined fixtures, not an intelligence/quality evaluation.
    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        call_id="search-1",
                        name="search_docs",
                        arguments={"query": "缓存方案 A 和 B"},
                    ),
                ],
            ),
            ScriptedStep(events=(ModelComplete(response=answer),), check=check_results),
        ]
    )
    agent = Agent(
        name="researcher",
        revision="1",
        model=model,
        instructions="根据资料比较方案；引用来源 ID；资料不足时说明。",
        tools=[search_docs],
        output_type=Report,
    )
    context = RunContext(tenant_id="offline", user_id="reader")
    async with Runtime(agents=[agent]) as runtime:
        if streamed:
            async with runtime.stream(
                agent, "比较缓存方案 A 和 B", session_id="research", context=context
            ) as stream:
                async for event in stream:
                    print(event.type)
                outcome = await stream.result()
        else:
            outcome = await runtime.run(
                agent, "比较缓存方案 A 和 B", session_id="research", context=context
            )
        if not isinstance(outcome, Completed):
            raise AssertionError(f"Unexpected outcome: {outcome.status}")
        report = outcome.output
        assert isinstance(report, Report)
        assert set(report.source_ids) == {source.id for source in retrieved}
        assert "单进程" in report.conclusion and "共享" in report.conclusion
        assert len(model.calls) == 2 and len(retrieved) == 2
        page = await runtime.inspect(outcome.run_id, context=context)
        assert [op.status for op in page.snapshot.operations] == ["succeeded"]
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(research(streamed=args.stream)))

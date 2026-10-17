# 快速入门

使用 Python 3.11–3.14。以下步骤在新目录取得源码，不需要模型账号、Key、Docker 或预算。Python 3.15 仅有探测记录，未列入支持范围。

## 从源码安装

```sh
git clone https://github.com/drov-ai/drov.git
cd drov
uv --version
uv sync --locked
uv run --locked python -c "import drov; print(drov.__version__)"
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

仓库要求 uv `0.13.0`；若本机尚未安装，按[开发说明](../development.md#环境与安装)在项目内建立隔离工具环境。预期版本输出为 `0.1.0a0`；检索例子返回 `Report`，包含来源 `cache-a`、`cache-b`，流式例子还打印事件。源码默认分支会推进；需要精确复现时先选择验证记录中的提交，再执行锁定安装。

包名与发布权限仍待 T-18 核验，目前使用源码或本地 wheel，不提供已可用的公开 PyPI 安装承诺。

## 一个完整的 Agent

将以下内容保存为仓库根目录的 `hello_drov.py`，运行 `uv run --locked python hello_drov.py`。它调用一个真实 Python 函数，模型回答由离线夹具预设。

```python
import asyncio
from dataclasses import dataclass

from drov import Agent, Completed, RunContext, Runtime, tool
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall


@dataclass
class Report:
    total: int


@tool(name="add", effect="read")
def add(left: int, right: int) -> int:
    return left + right


async def main() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        call_id="add-1",
                        name="add",
                        arguments={"left": 12, "right": 18},
                    )
                ],
            ),
            ModelResponse(message='{"total":30}', finish_reason="completed"),
        ]
    )
    agent = Agent(
        name="calculator",
        revision="1",
        model=model,
        tools=[add],
        output_type=Report,
    )
    context = RunContext(tenant_id="demo", user_id="reader")
    async with Runtime(agents=[agent]) as runtime:
        outcome = await runtime.run(
            agent,
            "12 + 18",
            session_id="first",
            context=context,
        )
        assert isinstance(outcome, Completed)
        assert outcome.output == Report(total=30)
        inspection = await runtime.inspect(outcome.run_id, context=context)
        assert inspection.snapshot.operations[0].status == "succeeded"
        print(outcome.output)


if __name__ == "__main__":
    asyncio.run(main())
```

预期输出为 `Report(total=30)`。预设模型不证明推理质量；完整检索例子还核对实际工具结果与最终引用。[模型接入](integrations.md)说明如何选择供应商和开始真实验证。

## 按需安装

下面是仓库内的锁定依赖选择。extras 可组合；不需要为只读 SDK 安装服务、MCP 或云沙箱。

| 需要什么 | 在仓库执行 | 首个离线检查 |
| --- | --- | --- |
| 核心 SDK | `uv sync --locked` | `examples/research_offline.py` |
| Responses 模型客户端 | `uv sync --locked --extra openai` | `examples/openai_responses_offline.py` |
| 本地 stdio MCP | `uv sync --locked --extra mcp` | `examples/mcp_stdio_offline.py` |
| HTTP / 工作台 | `uv sync --locked --extra server` | `examples/http_offline.py` |
| 可选 E2B 适配器 | `uv sync --locked --extra e2b` | 默认测试不访问云；真实调用另需授权 |
| 构建此文档站 | `uv sync --locked --group docs` | `python scripts/build_docs.py` |

`uv sync` 会按所选组合同步环境；需要多个 extra 时同时指定。Docker 使用核心 SDK 的本地执行器，无额外 Python extra，但必须具备[受测后端条件](sandbox.md)。

接下来运行[三个参考应用](examples.md)，或阅读[运行原理](concepts.md)。

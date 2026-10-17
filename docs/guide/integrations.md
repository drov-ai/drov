# 模型与 MCP

## 从离线客户端开始

```sh
uv run --locked --extra openai python examples/openai_responses_offline.py
uv run --locked --extra openai python examples/openai_responses_offline.py --stream
uv run --locked --extra openai python examples/openai_responses_offline.py --deepseek
```

以上只访问合成 loopback 服务，不读取模型 Key。openai extra 安装官方客户端；`OpenAIResponses` 的协议、严格输出、用量、错误和有序历史已有离线验证。DeepSeek 使用独立 `DeepSeekResponses` 方言与能力声明，不把更换 base URL 当作任意 OpenAI 兼容服务的支持保证。

## 准备真实模型调用

在开始前确定：服务、准确 model ID、上下文/工具/结构化输出能力、每批费用上限和停止条件。Key 仅在本机环境或凭据管理器配置；不写入仓库、聊天、工具参数或沙箱，不打开请求正文 dump。不要用本页离线命令代替真实模型质量验收。

适配器配置与应用代码见[OpenAI 协议决定](../decisions/0022-openai-responses-adapter.md)和[DeepSeek 决定](../decisions/0033-deepseek-responses-and-evaluation.md)。当前 OpenAI 真实模型未验证；DeepSeek 的固定合成任务报告达到门槛，但 rolling alias、提示与预算绑定了结果，仍有三项失败。新批次需要新的显式预算与登记，不能自动续跑历史付费评测。

自定义 ModelAdapter 实现 `generate(ModelRequest)` 的异步事件协议，并提供 `capabilities` 与资源关闭语义。模型适配只处理供应商映射，不能自行执行工具、写 Memory 或绕过 Runtime 审批。协议定义见[源码](../../src/drov/models/protocol.py)。

## 本地 stdio MCP

```sh
uv run --locked --extra mcp python examples/mcp_stdio_offline.py
uv run --locked --extra mcp python examples/mcp_stdio_offline.py --stream
```

真实本地 MCP 服务由样例启动，退出后关闭。`MCPStdio.discover` 短暂发现并冻结工具/schema/启动配置；连接装配由 ResourceBinding 管理，运行时配置变化会拒绝。工具效果必须由宿主通过 MCPToolPolicy 指定，缺省 unknown；审批、预算、单次派发和核查复用 Python 工具机制。

首版不支持远程 HTTP MCP、OAuth 或任意热更新发现。stdio 服务是宿主进程，可能获得传给它的环境/文件权限；它不是 Docker 沙箱，不能把未经信任的启动命令当作隔离执行。真实服务接入前核实仓库、绝对可执行文件、允许工具和最小环境变量；不要把模型 Key 顺便传给工具服务。完整接口见[MCP 契约](../planning/api-draft.md#mcp-stdio-toolst-12)。

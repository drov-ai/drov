# 离线检索、上下文与 Memory 检查

在仓库根目录完成 `uv sync --locked` 后运行，不需要账号、API Key 或私有资料：

```sh
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

例子对应 S-01-A：真实 Python 工具从自有合成语料检索 A/B 两条资料，ScriptedModel 检查实际工具结果，再返回预设 JSON；最终输出严格校验成 Report，并核对引用集合与实际来源一致。`--stream` 用相同 Runtime 入口打印已提交事件，随后读取相同结果。

这是可执行的离线契约验证，回答是预设夹具，不证明真实模型的推理质量。运行只用内存存储；SQLite、去重、取消、正文授权与失败提交另由集成测试验证。外部 Python 工具支持 read/write/unknown，写入/未知工具单次派发并沿权限/审批执行；内置 Memory 使用专用事务路径。显式 Runtime 的 ASK 已支持持久审批，临时 Agent.run 仍失败为 interaction_unavailable；T-09 已交付未知效果/显式恢复；真实模型和 HTTP 尚未交付。完整范围与下一项见 [progress](../docs/planning/progress.md)。

T-07 的 S-01-C 预览/引用检查：

```sh
uv run --locked python examples/context_preview_offline.py
uv run --locked python examples/context_preview_offline.py --stream
```

实际工具返回长合成资料，脚本模型只检查带引用/版本/hash 的标记预览；类型化报告明确未读取全文。示例从 inspect 的摘要索引读取 get_context，核对来源版本、preview 处理、粗估与预算。来源引用定位已保存的操作事实，不额外执行工具或调用模型；正文需 include_content=True。这个例子不证明真实模型会正确理解预览。

T-08 的 S-02 跨进程 Memory 检查：

```sh
uv run --locked python examples/memory_offline.py
uv run --locked python examples/memory_offline.py --stream
```

父进程在本地临时目录创建 SQLite 路径，依次启动七个独立进程：保存、新会话读取、其他用户/租户/Agent 隔离、删除、删除后新会话读取。每个子进程重建 Agent/Runtime/模型对象，检查实际工具结果、来源版本和 memory.changed 事务事件；父进程核对不同 PID，完成后清理夹具库。资料与回答均为自有合成夹具；脚本断言正确注入与读取，不构成真实模型记忆质量证据。无需账号/Key/付费调用。

首版计划在原三类参考应用中加入代码执行变体；T-10A/T-10B 将交付真实 Docker/Linux 离线示例，目前没有对应文件。预设模型代码只证明机制，T-11/T-16 的真实模型生成/实际执行结果单独评测；设计片段不能作为当前可运行例子，见 [参考应用](../docs/planning/reference-scenarios.md)。

T-09 的审批/输入子范围：

```sh
uv run --locked python examples/interactions_offline.py
uv run --locked python examples/interactions_offline.py --stream
```

批准/拒绝两例各有四个独立进程，重建同 revision 的 Agent/SQLite Runtime：提出 HumanInput → 合法回答生成固定只读调用 → 批准/拒绝 → 重复原响应。父进程核对原回执、实际工具返回与 1/0 次计数，临时目录自动清理；计数文件仅为夹具记录。它不是外部业务写入/unknown 故障验收，完整外部写入/核查请使用下方 operations_offline；预设回答不证明真实模型质量。

T-09 完整外部审批/核查：

```sh
uv run --locked python examples/operations_offline.py
uv run --locked python examples/operations_offline.py --stream
```

五组自有合成场景各建立一个仅 loopback、随机端口的独立事项服务，批准/拒绝、补充输入、回包丢失核查及未知时取消各用 3–4 个新 SDK 进程；服务记录实际写次数，核查/重复回执不多写。事项服务刻意非幂等，作为证据夹具，正常结束后关闭并清理临时目录；不是部署教程或正式业务服务。硬崩溃/逐故障矩阵仍按 T-10，预设模型不证明真实模型质量。

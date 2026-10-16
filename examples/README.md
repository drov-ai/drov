# 离线检索、上下文与 Memory 检查

T-11 新增 [openai_responses_offline.py](openai_responses_offline.py)：`uv run --locked --extra openai python examples/openai_responses_offline.py`（可加 `--stream`）。真实官方客户端只访问随机 loopback HTTP/SSE，使用显式合成凭据；本地函数工具 → 实际返回值 → 严格 Report 经同一 Runtime，结果为36。没有外部模型或账号调用，不能据此宣称真实模型质量。完整适配/历史/错误/安装证据见 [专项验证](../docs/validation/t11-openai-responses.md)。

T-10A 新增 [artifact_inputs_offline.py](artifact_inputs_offline.py)：真实 SDK 文件接收、上下文引用、关闭后以新 Runtime 授权读取并校验 hash，模型是 ScriptedModel，仅描述元数据，不执行 Python 分析/导出。POSIX 下可运行 `uv run --locked python examples/artifact_inputs_offline.py` 及 `--stream`；Windows 文件字节控制尚未验收，原五个核心示例不受影响。

在仓库根目录完成 `uv sync --locked` 后运行，不需要账号、API Key 或私有资料：

```sh
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

例子对应 S-01-A：真实 Python 工具从自有合成语料检索 A/B 两条资料，ScriptedModel 检查实际工具结果，再返回预设 JSON；最终输出严格校验成 Report，并核对引用集合与实际来源一致。`--stream` 用相同 Runtime 入口打印已提交事件，随后读取相同结果。

这是可执行的离线契约验证，回答是预设夹具，不证明真实模型的推理质量。运行只用内存存储；SQLite、去重、取消、正文授权与失败提交另由集成测试验证。外部 Python 工具支持 read/write/unknown，写入/未知工具单次派发并沿权限/审批执行；内置 Memory 使用专用事务路径。显式 Runtime 的 ASK 已支持持久审批，临时 Agent.run 仍失败为 interaction_unavailable；T-09 已交付未知效果/显式恢复；真实模型质量和 HTTP 尚未验证；Responses 离线协议适配见下方新增例子。完整范围与下一项见 [progress](../docs/planning/progress.md)。

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

[sandbox_offline.py](sandbox_offline.py) 已实现真实 Docker/Linux CSV 输入 → 默认审批 → Python 统计/CSV/PNG → 同一 Agent 最终回答 → Runtime 关闭后重新授权下载/hash。显式参数与实际镜像准备见 [sandbox 说明](../sandbox/README.md)，--chart 使用分析镜像，--stream 覆盖 SDK 流；目录必须新建，运行记录持久保留。真实测试还验证同进程两次独立环境及显式引用复用。模型生成的是预注册夹具代码，只证明机制；T-11/T-16 的真实模型生成质量另评，T-10B 完整隔离/故障已独立验收，证据见 progress。可选 T-10C 后再复用同一协议验证 E2B；参考应用继续检验通用性，见 [案例](../docs/planning/reference-scenarios.md)。

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


## MCP stdio 离线闭环

安装mcp extra后运行 `python examples/mcp_stdio_offline.py` 或加 `--stream`。示例由 [本地官方SDK服务](mcp_stdio_fixture.py) 提供两页工具，使用确定性ScriptedModel；默认unknown工具先持久暂停、批准后只调用一次，退出关闭预检/运行两个进程。无真实模型、账号或费用。该服务只含合成资料，带故障模式供测试，不是生产MCP服务。API/限制见 [MCP接口](../docs/planning/api-draft.md#mcp-stdio-toolst-12)。


HTTP服务安装`drov[server]`后运行`python examples/http_offline.py`或加`--stream`。示例在进程内启动真实127.0.0.1服务，使用本次随机头令牌驱动检索/Memory/人工输入与审批三场景，关闭时释放自有Runtime；不打印令牌、不读取Key、不调用模型服务。与SDK/stream及CSV/PNG的实际对照见 [T-13验证](../docs/validation/t13-http.md)，工作台另属T-14。

## 离线维护合成例

运行 `python examples/maintenance_offline.py` 或加 `--stream`。示例只在自动创建的临时合成库执行：实际SDK完成一个Run、停止Runtime、离线预览/逻辑清理、重开后旧Session/请求键返回ContentGone，模型夹具调用计数仍1。POSIX还实际接收合成CSV，在逻辑删除后另行预览/回收并确认字节计数及重复恢复为零；Windows沿既定字节范围仅运行数据库子范围。无需账号、Key、Docker或预算；core-only wheel/sdist安装检查包含两种入口。它没有操作真实用户库或实现完整备份恢复；管理入口及限制见 [API](../docs/planning/api-draft.md#离线-session-逻辑清理t-15-首批)，整卡状态见progress。

`python examples/abandonment_offline.py` 或加 `--stream`：合成模型提出一个必须审批的工具，实际SDK/stream暂停；关闭Runtime后显式离线放弃，再重开确认原批准/恢复不能执行，模型夹具计数仍1、实际工具调用0，正文清理后最小管理事实仍可查。仅操作临时样本，无外部账号/Key/费用，已纳入core-only构件安装验证；它不证明外部操作未执行或沙箱已销毁。

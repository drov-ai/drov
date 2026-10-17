# Python API

本文导航已实现入口；精确类型与状态语义分别维护在源码、[API 契约](../planning/api-draft.md)和[运行契约](../planning/runtime-contract.md)。开发快照的公共接口仍可能变化，数据扩展会拒绝不兼容的旧 SDK，不能直接降级覆盖数据库。

## 定义与配置

| 入口 | 用法与约束 |
| --- | --- |
| `Agent` | 传入 ModelAdapter；注册时显式 `name`/`revision`；可配置 tools、output_type、Memory、ContextPolicy、Limits |
| `@tool(name=...)` | 显式工具名；Python 参数/返回必须可编译为受支持 schema；`effect="read"/"write"/"unknown"`，默认 unknown |
| `RunContext` | 由可信宿主传入 tenant_id/user_id，不能从模型参数获取 |
| `Limits` | Run 只能收紧管理员上限；write_tool_retries 固定为 0 |
| `ModelCapabilities` | 显式声明 text/tools/structured_output/streaming/context_window；未知窗口必须显式给输入预算 |
| `PermissionPolicy` / `OwnerAuthorizer` | 静态工具决定与当前资源授权；应用拒绝优先 |
| `SQLiteStorage` / `MemoryStorage` | 持久/内存事务后端；用 Runtime 管理生命周期 |
| `ResourceBinding` | owned / borrowed / factory，明确连接与关闭归属 |

受支持的类型、联合、严格解析与 JSON 输出规则见[生成 schema](../reference/schema-v1.json)。Python `Agent[Report]` 的结果需要绑定具体 Report 类型；schema 文件不是运行验证。

## Runtime 入口

| 方法 | 结果与用途 |
| --- | --- |
| `start(agent, input, session_id=..., context=..., request_key=..., artifacts=...)` | RunRef；同 scope 的旧 request_key 复用原接收事实，不再次执行 |
| `run(...)` / `wait(run_id, context=...)` | 等到暂停或四种终态之一；Completed.output 绑定 Agent 输出类型 |
| `stream(...)` / `events(run_id, ...)` | 异步上下文流 / 持久事件观察；退出不取消，临时 delta 不是恢复日志 |
| `cancel(run_id, context=...)` | 保存取消请求；未停止的实际调用可能继续占槽位 |
| `inspect(run_id, include_content=False, ...)` | 分页快照、操作与事件；默认元数据 |
| `respond(run_id, request_id=..., response=..., context=...)` | 保存不可变回执；支持审批、输入、核查响应 |
| `resume(...)` | 响应并等待原计划继续；不能重放已有工具 |
| `verify(run_id, request_id=..., context=...)` | 调用已注册的只读核查器；不重派发原写入 |
| `recover(run_id, context=..., wait=True)` | 显式恢复，沿原 checkpoint/租约继续 |
| `get_context(run_id, model_call_id, include_content=False, ...)` | 实际模型输入的来源、版本、排除与预览清单 |
| `sessions(...)` / `session(session_id, ...)` | 当前授权范围内的分页历史 |
| `get_artifact(artifact_id, include_content=False, ...)` | 文件元数据，正文权限另控名称 |
| `read_artifact(ref, context=...)` | 当前授权的 ready 字节，校验损坏不返回假成功 |
| `export_run(run_id, include_content=False, artifact_ids=(), ...)` | 分页观察副本，文件显式选择；不是备份或模型重放 |

所有资源查询都必须提供可信 context，省略处仍受原方法签名约束。自动生成的[实际签名表](api-signatures.md)包含准确参数、默认值和源码入口。

## 结果与交互

`Completed`、`Failed`、`Suspended`、`Cancelled` 从 drov 导入。只有 Completed 说明已通过输出类型校验。Failed.error 是结构化错误；Suspended.pending 表明当前需要审批、输入或核查，不能忽略后假装已完成。

`ApprovalDecision(approved=...)`、`InputResponse(value=...)`、`ReconciliationDecision(...)` 是三类响应。核查的 evidence 必须来自独立事实，不能仅凭模型声称“完成”。模型调用原始协议数据不作为公开思维链。

## 集成与数据

| 模块 | 已实现入口 / 后续阅读 |
| --- | --- |
| `drov.models` / `.openai` | ScriptedModel / OpenAIResponses；DeepSeekResponses 见模型指南 |
| `drov.integrations.mcp` | MCPStdio / MCPTool / MCPToolPolicy；仅 stdio |
| `drov.artifacts` | ArtifactInput / ArtifactRef / ArtifactLimits / LocalArtifacts；文件字节层仅 POSIX |
| `drov.sandbox` | PythonSandbox / DockerSandbox / SandboxLimits / SandboxResult |
| `drov.integrations.e2b` | 可选 E2BSandbox / E2BLimits；需独立模板与费用控制 |
| `drov.server` | DrovApp / ServerLimits；[实际 OpenAPI](../reference/openapi-v1.json) |
| `drov.storage.maintenance` | SQLiteMaintenance；[离线维护](maintenance.md) |

HTTP DTO、Limits 默认值与 SQL 扩展的生成参考位于[已实现定义](../reference/README.md)。不要绕过 Runtime 使用私有 stage/dispatcher 方法，它们不提供独立授权的用户 API。

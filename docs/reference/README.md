# 已实现的定义与数据契约

T-02 至 T-08 提供类型/schema、协议、事务存储、Runtime 生命周期、工具权限、只读 Agent 循环、正文投影、上下文选择/清单及事务 Memory；完整交互/恢复及真实模型适配器仍未实现。公共入口语义见 [API](../planning/api-draft.md)，T-06 细节见 [ADR-0010](../decisions/0010-read-only-agent-loop.md)。

## 模块入口

| 入口 | 已实现内容 |
| --- | --- |
| drov | Agent 定义、Limits/ModelCapabilities/ContextPolicy/Memory 配置描述、RunContext/ToolContext、四类结果、三类交互响应、Runtime/ResourceBinding、tool/PermissionPolicy/OwnerAuthorizer |
| drov.runtime | Runtime open/aclose/异步上下文、registration/validate_record、storage/dependencies/active_slots；AgentRegistration/ResourceBinding；start/run/wait/cancel/inspect/events/stream/get_context 已实现；恢复/交互入口尚未实现 |
| drov.types | 状态/结果/交互/事件/上下文/分页 DTO、Usage、RunError、parse_outcome/parse_response/parse_event、outcome_adapter |
| drov.models | ScriptedModel/ScriptedStep 离线预设序列；额外调用拒绝，不使用 Key |
| drov.models.protocol | ModelAdapter、ModelRequest/Response、消息/工具调用、delta/complete、collect_response |
| drov.tools.schema | compile_tool、compile_type、compile_output、strict_schema、ToolDefinition/JsonCodec |
| drov.tools | @tool/不可变 PythonTool/PermissionPolicy；compile_tool 仍是 schema-only；真实派发只由私有内核接点使用 |
| drov.auth / drov.errors | Authorizer/AuthAdapter、ToolResource/OwnerAuthorizer、错误与 TransientToolError；HTTP 认证仍未实现 |
| drov.storage | MemoryStorage / SQLiteStorage；异步 open/aclose、start/查询、原子 transaction 接点；内部协议尚不稳定 |

Memory 是配置对象，MemoryStorage 是事务内存后端；配置 Memory 的注册 Agent 自动导出三个内置工具，实际检索/事务执行见 [ADR-0012](../decisions/0012-transactional-memory.md)；Agent 只定义行为，构造不会执行。存储描述只在 open/异步上下文进入时获取连接和锁；完整记录由内核做授权投影，不能直接作为 API 响应。compile_tool 只创建描述，@tool 创建含 callable 的 PythonTool；private execution/operations 接点真实派发 read 并记录事务。外部 Python write/unknown 仍拒绝，ASK 不制造交互 ID，只读公共执行入口已接通；ASK 明确 failed/interaction_unavailable，完整交互仍待 T-09。provider_items 仅供适配器往返，不用于向用户显示内部协议数据。

## 生成与校验

[schema-v1.json](schema-v1.json) 包含独立 JSON Schema 文档集合；各 `$ref` 在对应文档内解析，不是将整个集合视为一个 schema。RunOutcome 的生成文档使用 JSON 输出边界；应用可通过 `TypeAdapter(RunOutcome[Report])` 或 `outcome_adapter(Report)` 绑定自己的输出类型。

```sh
uv run --locked python scripts/generate_schemas.py
uv run --locked python scripts/generate_schemas.py --check
```

字段/联合来自 Python 定义。schema 不能完整表达所有跨字段规则（例如省略 result、pending 与 reason 一致）；实际入口必须继续用 Python 校验。

[storage-schema-v1.sql](storage-schema-v1.sql) 从实际 DDL 生成，供检查表/索引；不是独立初始化或迁移脚本。SQLiteStorage.open 同事务初始化格式、schema 与去重 metadata；不要绕过该入口手工建表。事务/版本/锁的行为见 [ADR-0007](../decisions/0007-transactional-storage.md) 和 [验证](../validation/t03-storage.md)。

JSON 输入使用 WireModel.from_json 或 parse_*；工具使用 codec.parse/bind_arguments。这些入口拒绝重复键、非有限数字和类型强转。直接调用继承的 Pydantic 解析接口不承担 Drov 的重复键拒绝保证。

```python
from drov import ApprovalDecision, Limits
from drov.types import ReconciliationDecision, parse_response

limits = Limits(model_retries=0)
approval = parse_response(ApprovalDecision(approved=True).model_dump_json())
evidence = ReconciliationDecision(resolution="applied", evidence="已核实效果")
assert not evidence.has_result
assert "result" not in evidence.model_dump()
```

此例只验证数据类型，不执行审批或外部操作。日期、数字、字符串和对象是否敏感仍需内核授权投影；冻结 DTO 不等于深层不可变。

## 可运行只读入口

完整 [离线检索示例](../../examples/README.md) 使用实际 Python 工具、ScriptedModel 和严格 Report 输出，支持普通与 stream 入口。run/wait/stream 返回绑定 output_type 的结果；inspect/events 默认仅元数据，正文需 read/view_content，provider_items 不展示。流退出不取消执行，显式 cancel 记录取消事实；RunStream.result 与 wait 使用同一路径。

当前仅支持 context 本人的 tenant/user 查询作用域；schema-only 及外部 write/unknown 在接收前拒绝；配置 Memory 的 Agent 已可执行内置事务工具。inspect.context_manifests 返回当前事件页的有界清单索引（不带 sources），get_context 默认完整来源元数据，include_content=True 带选入内容/预览；provider 正文始终隐藏。旧历史按完整 Run 组选取，原结果不因预览改写；恢复/审批等入口没有空实现。完整 API 表中的后续条目须按状态说明使用。

## 待实施的沙箱接口

首版最小 Python 沙箱已纳入 C-17，T-10A/T-10B 才实施；PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult **当前不可导入**，不加入上面的已实现入口/schema 表。接口设计见 [API 草案](../planning/api-draft.md)，范围变化见 [ADR-0013](../decisions/0013-first-release-code-sandbox.md)。

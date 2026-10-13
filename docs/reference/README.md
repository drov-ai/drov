# 已实现的定义与数据契约

T-02 至 T-04 提供类型/schema、协议、事务存储与 Runtime 注册/资源生命周期，尚无 Agent 执行入口、@tool 或真实模型适配器。公共入口语义仍以 [API 草案](../planning/api-draft.md) 为准。实际边界见 [ADR-0006](../decisions/0006-python-contracts.md)。

## 模块入口

| 入口 | 已实现内容 |
| --- | --- |
| drov | Agent 定义、Limits/ModelCapabilities/ContextPolicy/Memory 配置描述、RunContext/ToolContext、四类结果、三类交互响应、Runtime/ResourceBinding |
| drov.runtime | Runtime open/aclose/异步上下文、registration/validate_record、storage/dependencies/active_slots；AgentRegistration/ResourceBinding；没有运行或恢复入口 |
| drov.types | 状态/结果/交互/事件/上下文/分页 DTO、Usage、RunError、parse_outcome/parse_response/parse_event、outcome_adapter |
| drov.models.protocol | ModelAdapter、ModelRequest/Response、消息/工具调用、delta/complete、collect_response |
| drov.tools.schema | compile_tool、compile_type、compile_output、strict_schema、ToolDefinition/JsonCodec |
| drov.auth / drov.errors | 异步身份/授权协议与类型化异常；不代表已有权限引擎或服务 |
| drov.storage | MemoryStorage / SQLiteStorage；异步 open/aclose、start/查询、原子 transaction 接点；内部协议尚不稳定 |

Memory 是配置对象，MemoryStorage 是事务内存后端；模型 Memory 工具和检索装配尚未实现；Agent 只定义行为，构造不会执行。存储描述只在 open/异步上下文进入时获取连接和锁；完整记录只能交给后续内核做授权投影，不能直接作为 API 响应。工具描述通过 compile_tool 创建，T-05 才添加装饰器与调用管理。provider_items 仅供适配器往返，不用于向用户显示内部协议数据。

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

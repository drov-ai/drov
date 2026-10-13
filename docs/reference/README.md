# 已实现的定义与数据契约

T-02 提供类型、校验与协议，尚无 Runtime、Agent.run、@tool 或真实模型适配器。公共入口语义仍以 [API 草案](../planning/api-draft.md) 为准。实际边界见 [ADR-0006](../decisions/0006-python-contracts.md)。

## 模块入口

| 入口 | 已实现内容 |
| --- | --- |
| drov | Agent 定义、Limits/ModelCapabilities/ContextPolicy/Memory 配置描述、RunContext/ToolContext、四类结果、三类交互响应 |
| drov.types | 状态/结果/交互/事件/上下文/分页 DTO、Usage、RunError、parse_outcome/parse_response/parse_event、outcome_adapter |
| drov.models.protocol | ModelAdapter、ModelRequest/Response、消息/工具调用、delta/complete、collect_response |
| drov.tools.schema | compile_tool、compile_type、compile_output、strict_schema、ToolDefinition/JsonCodec |
| drov.auth / drov.errors | 异步身份/授权协议与类型化异常；不代表已有权限引擎或服务 |

Memory 是配置对象，不是已经可用的存储；Agent 只定义行为，构造不会执行。工具描述通过 compile_tool 创建，T-05 才添加装饰器与调用管理。provider_items 仅供适配器往返，不用于向用户显示内部协议数据。

## 生成与校验

[schema-v1.json](schema-v1.json) 包含独立 JSON Schema 文档集合；各 `$ref` 在对应文档内解析，不是将整个集合视为一个 schema。RunOutcome 的生成文档使用 JSON 输出边界；应用可通过 `TypeAdapter(RunOutcome[Report])` 或 `outcome_adapter(Report)` 绑定自己的输出类型。

```sh
uv run --locked python scripts/generate_schemas.py
uv run --locked python scripts/generate_schemas.py --check
```

字段/联合来自 Python 定义。schema 不能完整表达所有跨字段规则（例如省略 result、pending 与 reason 一致）；实际入口必须继续用 Python 校验。

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

# ADR-0006：Python 类型、JSON 边界与模型协议

- 日期：2026-10-13
- 状态：T-02 实施决定；类型/schema 已实现，执行语义仍由后续任务验收
- 关联：细化 P4 API/运行契约 v3，不增加能力类别；旧 P4 指纹保留

## 原因

T-02 需要让存储、工具、模型和服务共享可校验的数据，而不是各自定义相似字段。Python 类型可表达的能力多于供应商 JSON schema；仅调用 Pydantic 的 schema 生成器不能保证本地严格校验、可信字段隔离或泛型结果正确。

## 决定

1. WireModel 使用严格校验、禁止额外字段、有限数字和 UTC RFC3339 时间；公共 JSON 边界使用 `from_json` / `parse_*` / 工具 codec，额外拒绝重复键及 NaN/Infinity。Pydantic 原生 `model_validate_json` 不承担重复键拒绝契约。schema_version/durable 等常量同时检查 JSON 类型，不能把 true 当 1。
2. RunOutcome[T] 使用 typing-extensions 的 TypeAliasType，让 Python 3.11 下 `TypeAdapter(RunOutcome[str])` 保留输出类型和判别字段；将已锁定的 typing-extensions 4.16.0 声明为直接核心依赖，未升级其他版本。交互响应 serialization 保留省略 result 与显式 null 的区别。
3. 对象 schema 总是关闭额外字段。支持普通 dataclass/BaseModel 的声明字段及常见 Field 数值/长度/正则约束；明确拒绝递归、RootModel、alias、自定义校验/序列化/构造钩子、嵌套 Annotated 约束和自定义 schema。参数/最终输出不支持动态 dict；工具结果支持字符串键 dict。供应商严格模式把默认字段变成必填并说明默认值，不把 null 当默认；动态 dict 与未知结构明确拒绝。
4. ToolContext 只按类型识别，可信身份与 deps 不出现在模型参数 schema；T-02 的 compile_tool 只生成/校验定义和绑定值，不持有执行入口、不调用工具。@tool、权限决策、线程执行和 verifier 留在 T-05。
5. 模型统一为 ModelRequest → delta/complete 流。只有完整 ModelResponse 可提供工具计划；complete 后多余事件、缺少 complete、重复 call_id、截断响应携带计划均拒绝。provider_items 是严格 JSON 的不透明往返数据，不进入 ContextManifest 正文。适配器异常保留为异常，不能用部分文本伪造完成。
6. Agent 当前接收 ModelAdapter 实例；实例是否借用/自有由 T-04 资源装配决定。name/revision 成对提供，持久化必填。未知 context_window 必须显式提供 max_input_tokens；已知窗口不允许预算挤占输出预留。配置构造不请求模型、不打开存储、不关闭客户端。
7. v0.1 write_tool_retries 固定为 0；应用级幂等保证不自动开放框架重派发。AuthAdapter 先接收宿主提取的凭据字符串，Authorizer 使用 actor/action/resource；HTTP 认证、撤权与真实授权在 T-13 验证。

## 替代方案与代价

- 接受任意 Pydantic 特性会扩大首期兼容面，使 schema 与解析不同步；选择显式子集，应用需要先将复杂类型转换为公开 JSON 对象。
- 只依靠 JSON schema 会遗漏跨字段、归属和省略字段规则；保留 Python 验证器，schema 文档明确不是所有语义的完整表达。
- 将可信上下文作为参数 schema 的隐藏属性仍可能被模型填写；选择独立绑定。它不替代宿主认证，也不证明 HTTP 身份已安全实现。
- 泛型结果只作为静态注解较省依赖，但运行时可能退化为 Any；选择已有锁中依赖的直接声明和真实泛型 codec 测试。

冻结对象只阻止属性重赋值，不提供深层不可变或安全沙箱。事件 data、错误摘要与上下文 source ID 的授权投影仍由内核实现；类型无法自动判断任意字符串是否含秘密。JSON schema 与参数 schema 是不同边界：前者描述公开数据，后者严格限制模型可填写值。

## 影响与证据

- [API 草案](../planning/api-draft.md)：维护用户接口、已实现与未来入口的区别。
- [工程方案](../planning/engineering.md)：维护子集、依赖与默认值。
- [质量计划](../planning/quality-plan.md)：增加已交付类型/适配夹具的检验范围；B-15 全部上下文规则仍未完成。
- [生成 schema](../reference/schema-v1.json)：由 Python 定义生成，不手改第二套类型清单。
- [T-02 验证](../validation/t02-contracts.md)：命令、平台、实际通过项及限制。

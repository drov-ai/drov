# ADR-0011：完整轮次选择、结果预览与固定上下文清单

- 日期：2026-10-13（Asia/Shanghai）
- 状态：T-07 实施决定；验证见 [T-07 记录](../validation/t07-context.md)
- 关联：细化 ADR-0003/0005/0010、C-05/07、S-01-C、B-08/15；历史 P4 fingerprint 保留

## 原因

T-06 固定输入与历史事实已落地，但完整历史超过预算即失败，检查清单为空。需要在实际模型调用前确定选择结果、可核对来源和预览，同时保持工具计划与协议关系完整。T-07 不引入模型摘要、Memory 或恢复空入口。

## 决定

1. 旧 Run 的输入、所有已提交响应、工具结果与相应 provider_items 构成一个完整历史组；当前 Run 的输入和已提交协议链全部保护。系统规则、工具 schema、输出 schema 和请求协议开销也保护。最近完整组优先，装不下的组记录排除原因后继续尝试更旧组；最终仍按原会话顺序排列。不完整模型流不进入候选。
2. 原始工具结果继续保存在操作事实中。超出预览上限时，用 `drov.tool_preview` 包装文本摘录，包含 source_ref/source_version、原内容 SHA-256、原字节数和 truncated；不是合法原结果的半段 JSON。UTF-8 边界及 JSON 转义计入上限。保护输入过大时，共享的当前结果预览上限依次减半至完整引用/标记所需最小值；标记或其余保护内容仍装不下，则 context_overflow。历史预览使用配置上限，整个旧轮次不合预算则排除。
3. RunController 负责在单一读取/提交事务中形成候选、保存固定 ModelRequest、ContextManifest、context.prepared 事件和检查点。纯选择/预览在内部 context 模块；它不读取存储、不调用模型。拒绝输入同样保存 state=rejected 的输入/清单，事件带 context_overflow，模型步/attempt 不增加。只有提交成功才可能派发模型，重试不重新装配。
4. 清单保存来源 ID、实际记录版本或配置内容 hash、选入/排除原因与处理方式。全请求粗估包含全部序列化 JSON、每请求和每协议项开销；源级估算是单个内容的序列化字节贡献，不能直接相加当精确 token。供应商用量从该调用的已提交响应关联读取，不改写初始清单。Memory 未启用时 retrieval_status=disabled，不保留空置子预算；required/best_effort 在 T-08 实施。
5. get_context 默认返回来源元数据，正文另需 view_content；存储读取后再次授权，provider 正文始终不展示。context.prepared 同事务保存固定大小摘要；inspect.context_manifests 仅索引当前事件页的 context.prepared，sources=[]、content_visible=False；翻事件页即可遍历清单索引，详细来源通过 get_context 读取，避免加载详细输入/清单到每页。索引保留装配时的未知 reported_usage，get_context 才关联完整响应的已报告用量。旧 T-06 事件无摘要则不生成索引，仍可读取状态/事件。引用指向内部持久操作事实，不是 URL 或额外全文检索工具。
6. 开发快照的 ContextManifest schema_version 仍为 1，新增带默认值的 estimated_tokens/reported_usage，budget 允许 0 表示输出预留已用尽窗口。SQLite 表、记录布局版本、ModelRequest 和依赖锁不变。T-06 存量模型输入没有清单，不回填伪造来源；新清单按实际保存的 ID 查询。此 alpha 尚无公开存储兼容承诺，后续迁移由 T-15 管理。

## 替代、代价与影响

- 单条消息裁剪会留下孤立工具结果或脱离用户输入的回答；完整轮次更保守，可能排除仍有价值的个别内容。
- 全历史或模型摘要会分别造成长期溢出或增加调用/质量不确定性；确定性组选择不增加模型成本，但字节粗估不是 tokenizer 的硬上界。
- 每页返回完整清单会随会话长度膨胀；事件页索引有界，但显式详细清单仍随候选历史规模增长。当前需扫描完整已提交会话以列全排除来源，不承诺超长历史的性能。
- 来源引用可定位操作，不表示模型读过全文；S-01-C 示例明确 full_document_read=False。完整工具原值只在授权后的工具事件中显示，预览不改写已提交结果。

源 [运行契约](../planning/runtime-contract.md)、[API](../planning/api-draft.md)、[架构](../planning/architecture.md)、[工程](../planning/engineering.md)、[质量计划](../planning/quality-plan.md)、[案例](../planning/reference-scenarios.md) 同步。无依赖/能力范围扩张；真实 tokenizer/模型质量、Memory、审批、跨归属查询、HTTP/工作台仍按后续任务验收。

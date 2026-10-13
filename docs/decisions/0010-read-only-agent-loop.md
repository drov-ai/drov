# ADR-0010：只读 Agent 闭环、历史与授权订阅

- 日期：2026-10-13（Asia/Shanghai）
- 状态：T-06 实施决定；实际验证见 [T-06 记录](../validation/t06-agent-loop.md)
- 关联：细化 ADR-0005/0008/0009，沿用 API/运行契约 v3；不覆盖历史 P4 fingerprint，不增加后续能力空壳

## 原因

工具派发和资源所有权已经存在，但尚未连到公共运行入口。接收事务与调度之间的取消、完整模型计划与工具派发之间的提交失败、正文撤权及失败后的历史都需要实际闭环保证。同一时间戳不能用随机 Run ID 推断会话消息顺序。

## 决定

1. Runtime.start/run/wait/cancel/inspect/events/stream 与 Agent.run/run_sync 共用 RunController。接收前拒绝 schema-only、write/unknown、Memory 配置及未注册对象。临时入口克隆定义身份，使用内存存储，不修改原 Agent，不延续历史；其限额由应用提供。持久 Runtime 的 Agent 默认限额先与管理员上限取较严格值，显式 Limits 只覆盖其设置过的字段，不能放宽管理员上限。
2. 接收在 Runtime 持有强引用的 control Task 内完成。当前授权、去重在容量之前；同一接收事务内查键、预留槽位、保存 accepted。取消调用者等待时先确认事务和交接事实，已接受执行仍归 Runtime。调度失败保存 suspended/recovery；存储失败抛出 PersistenceUnavailable，不伪造终态。关闭先等控制事务，再取消执行；无法结束则保留资源/锁。控制与执行任务禁止自关闭。
3. 保存固定 ModelRequest 后，每个模型 attempt 提交独立 ID、次数及活动时长预留；完整流结束且协议合法后，ModelResponse、所有 operation_id、用量、事件和检查点同事务提交，再按计划顺序派发工具。只对 TransientModelError 有界重试，输入和 model_call_id 不变；普通错误/超时/非法协议不重试。不自动修复结构化输出，最终结果使用同一 codec。ScriptedModel/ScriptedStep 是离线预定事件/检查/错误适配器，额外调用明确失败，不代表真实推理。
4. 接收记录是一次用户输入的唯一事实来源；Checkpoint.session_sequence 使用 Session 版本，在同一接收事务保存，排序不依赖墙钟或随机 ID。历史从完整提交的模型消息/计划与操作结果投影，不复制重复用户输入、不纳入临时增量。终态事务把 prepared 操作保存为 not_executed；计划中尚未创建的操作通过终态依据投影为明确的 run_ended_before_dispatch 结果，不能伪造成功。旧无顺序号记录按原 created_at/ID 排在有序记录之前；此开发快照没有旧公开 Agent 循环，不承诺旧数据迁移。
5. Checkpoint.engine_schema=1，model_input 和 model_response 的内部 payload 各带 schema_version=1。模型步数、attempt 次数、工具派发次数和 active_seconds 保存在原 Run，不因失败或去重重置。调用前保守预留允许时长，正常返回按实耗结算；未知/未停止调用保留预留。只读调用超时且实际尚未停止时结束 Agent 推进，并继续持有实际 Task/Future；迟到结果不改变已保存终态。预算/绝对期限限制下一次派发；取消与完成以同一事务中的当前状态决定，终态 cancel 幂等。
6. run/stream 在接受前预检 start/read/view_content，wait 在返回前复核；inspect/events 默认为有限元数据，正文要求 read/view_content。工具参数/结果仅在正文事件中提供，provider_items 始终只作内部协议往返。订阅在每次发送前复核权限，缓存不授予正文访问。当前查询只支持 context 的 tenant/user 作用域；跨归属身份解析/审批仍属后续任务，不能凭 run_id 或 Authorizer 的全局 allow 绕过查询隔离。
7. RunEvents/RunStream 使用存储分页补读 durable 事件；只有追平当前暂停/终态快照才能结束，旧 suspended 事件不截断当前 running。临时增量独立有界缓冲，丢弃超大/超量项并提供 stream.gap；关闭/取消等待仅解除订阅。内核在有活动执行时以至多 50ms 轮询桥接工具事务通知，无无界 durable 队列。未知事件仅保留类型/ID/序号，不提供其任意正文。inspect 的 context_manifests 暂为空，get_context 尚未提供。
8. T-06 上下文采用有界的完整历史投影和保守字节粗估，工具/输出 schema 与原始协议项计入最终请求估算；装不下明确 context_overflow，不静默裁剪。T-07 再交付完整组选择、结果预览及来源清单，T-08 Memory，T-09 交互/显式恢复。当前所有 ASK（包括持久 Runtime）均 failed/interaction_unavailable，待授权计数 0，无虚假请求 ID。

## 替代与代价

- 直接让 start 调用者拥有接收/执行，会在断连时丢失交接；独立强引用任务增加关闭和失败处理，但不需要持久队列。
- 把流队列当事实源会受慢消费者影响；分页补读保留完整事实，代价是当前 SDK 有轮询读取开销。HTTP/SSE 的重连与背压仍需 T-13 验证。
- 只按时间/UUID 排序更简单，但相同时间戳会让新输入出现在旧历史前；持久 Session 顺序号增加内部数据字段。
- 提前实现裁剪/Memory/审批空入口会模糊任务验收；选择可执行的只读子集并明确拒绝，代价是长历史与 ASK 当前会失败。

## 影响

源 [API](../planning/api-draft.md)、[运行契约](../planning/runtime-contract.md)、[架构](../planning/architecture.md)、[工程](../planning/engineering.md)、[质量计划](../planning/quality-plan.md) 与 [S-01](../planning/reference-scenarios.md) 同步。SQLite 表结构和公共 DTO schema 不变，无新依赖。新实现只经本地离线测试，不宣称完整恢复、真实模型质量、HTTP 或跨平台 CI 已验证。

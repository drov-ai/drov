# ADR-0012：内置 Memory 的原子工具与有期限的输入快照

- 日期：2026-10-13（Asia/Shanghai）
- 状态：T-08 工作决定；实现和验证范围见 [T-08 记录](../validation/t08-memory.md)
- 关联：细化 ADR-0003/0005/0007/0010/0011，C-06/07、S-02、B-08/09/10；历史 P4 指纹不覆盖

## 原因

底层 MemoryRecord/CAS 已存在，但不足以支持“保存工具成功即已持久保存”，T-07 也尚未消费真实记忆来源。直接放开 Python 写工具会绕过后续审批/未知效果规则；检索与输入保存之间的删除竞争还可能注入过期内容。

## 决定

1. Memory 配置注入三个私有 MemoryTool 描述；逻辑名称/参数/返回值仍采用 API 草案，加入注册指纹及模型 schema。应用自定义工具不得占用这些名称，Memory 不成为外部 Python write 的许可。无 Memory 的既有指纹不变；过去不能执行的 Memory 定义在重建时使用新增 schema 指纹。
2. MemoryDispatcher 准备操作、规范参数及请求 hash；重复 operation_id 只返回既有事实，异参冲突。权限沿既有 deny/ask/allow/业务顺序检查，事务锁等待后复核、检查取消。最终授权在存储事务内执行；重入同一 Storage 会被拒绝，应用授权器应使用自己的权限数据。ASK 当前 failed/interaction_unavailable，无交互空壳。
3. 专用本地事务共同保存 Memory 修改、操作结果/来源、Run 摘要、预算/检查点及 memory.changed。不用外部 tool.dispatched/unknown 路径。一次实际本地尝试有独立 attempt_id，成功、memory_conflict 或输出超限消耗一次工具次数；参数/权限拒绝和完整回滚消耗 0。输出先校验后修改；内置操作无自动重试。超时若发生在已完成提交之后，读取并保留已提交结果；以后取消/失败不回滚旧成功。
4. 新建 expected_version=0；更新/删除使用实际版本。forget 不存在或已删除的键、版本不匹配返回 memory_conflict；墓碑不含正文且保留版本，精确 recall 可读后重建。普通检索只读 live，先在存储查询中限定 tenant/user/Agent，再按 updated_at DESC、key ASC 与 NFKC+casefold 完整子串匹配；不承诺语义检索或无限量性能。
5. 每次自动检索由 Run 持有一个真实 Task，required/best_effort 各按 Limits 期限；完成但超过期限也丢弃。超时取消，额外清理至多 100ms；未停止的异常后端抛 PersistenceUnavailable，模型计数 0，并保留任务/槽位/存储所有权。正常 best_effort 的 skipped 在现有 DTO 中表示为 retrieval_status=timeout；异常 failed、成功零条 empty。required 失败/超时保存 rejected 输入/清单，模型 step/attempt 为 0。
6. 输入保存事务内复核候选版本；删除/变化重查同 scope 的最近记录，旧来源明确排除并留版本，不重新启动后台读取。选入记忆作为 drov.memory user 数据消息，以完整消息与协议粗估计入 ContextPolicy 子预算和实际剩余量，然后选择旧历史。超大记忆整条排除。输入/清单提交后，删除不能改写已保存快照，模型重试不再检索；下一步骤重新检索。
7. 来源 ID 是 scope/key 的内容 hash，不把 Memory key 放进元数据 ID。显式 recall 的实际版本保存在 operation.memory_sources（schema=1、默认空列表），参与后续清单；其工具结果被预览时，来源也标 preview、不展示未完整注入的正文。默认 inspect/events/get_context 继续无正文投影，provider 正文不可见。

## 替代方案与代价

- 将 Memory 包装成普通 Python 写工具：不能把数据库写入与成功结果原子提交，且会提前引入未完成的外部写入语义；不采用。
- 在输入事务中启动有期限的子 Task 检索：Transaction 有任务所有权，容易重入/死锁；采用独立检索及同事务版本复核。
- 超时后后台补入、重试再检索或删除后改写旧输入：会改变同一次调用的问题与证据；不采用。
- 向量/外部 Memory 与自动提取：需要单独一致性、资源和质量方案，保持后续范围。

复核与子串扫描会增加本地读取量；详细清单随候选增长。截止时间依赖异步任务协作，不能强行抢占阻塞事件循环的后端；即使阻塞读取最终返回，也按完成时间拒绝迟到内容。内置 SQLite 仍使用现有事务/锁；无新依赖、DDL 或公共 DTO 变更。事务正确性不证明模型会主动调用正确工具或合理使用偏好。

## 影响

源 API、运行契约、工程/架构、质量、S-02、开发说明和进度同轮同步。T-09 的审批/显式恢复、T-10 的外部未知写入/进程崩溃、T-11/T-16 真实模型、T-15 历史清理及后续外部 Memory 仍未验收。

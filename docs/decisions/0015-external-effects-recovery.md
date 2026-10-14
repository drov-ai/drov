# ADR-0015：外部未知效果、只读核查与显式步骤恢复

- 日期：2026-10-14（Asia/Shanghai）
- 状态：T-09 工作决定；本轮验证见 [核查与恢复记录](../validation/t09-reconciliation-recovery.md)，硬崩溃专项仍由 T-10 验收
- 关联：细化 ADR-0005/0007/0009/0010/0014，C-08/09/11/12、S-03-A/B/C/D/E、B-01/04/05/07；历史 P4 指纹保留

## 原因

批准后开放外部写入必须同时处理“实际成功但回包丢失”、非法结果及取消；错误文本不能证明没有副作用。恢复只能基于已提交输入、计划和操作，不能重做整轮模型或把消费后的批准当成新派发许可。

## 决定

1. PythonTool 支持 read/write/unknown；read 保留明确瞬态重试，write/unknown 每次仅派发一次。派发后的异常（含 TransientToolError）、超时、取消和不可用输出保守记录 unknown，再持久暂停 reconciliation。已提交结果不重做。批准到期且确定尚未调用时保存 not_executed；不因错误字符串或“声明幂等”自动重写。
2. @tool 新增 verifier 与 verifier_revision="1"；verifier 为宿主显式注册的异步只读回调 `(ToolContext) -> ReconciliationDecision`，不能从模型创建查询目标。上下文沿原用户/operation_id/当前 dependencies；注册指纹仅在存在 verifier 时加入其 revision，无 verifier 的既有指纹结构不变。宿主变更函数/服务契约仍须更新 revision，不把元数据当安全证明。
3. verify 预检 read/view_content/respond/verify，并复核原执行者当前业务权限及 deny；ASK/required approval 不授权新写入，核查只读已发生操作。单 operation 一个实际 verifier Task、一个活动槽位、独立期限，无自动轮询；失败/超时返回 unresolved 的安全说明。未停止 Task 保留槽位/存储锁，迟到建议无法消费原已变更请求，回到调用者前再次授权。
4. 人工或 verifier 建议均经显式 respond/resume。applied 正常继续须有可校验的原工具结果；取消/预算收尾可仅提供效果证据。not_applied 要求原本地调用已停止；清除旧批准后重新按当前权限/审批处理原操作，累计工具次数不回退。unresolved 原子消费旧请求/回执、创建同 operation 的新请求、仍暂停；提醒过期不拒绝核查。非空证据/结果 schema 校验不等于框架独立证明业务事实，可信证据由宿主服务和获授权核查人负责。
5. 取消或预算耗尽时未知效果继续暂停。确认后仅按 cancel_requested/保存的停止原因收尾，不执行新的模型或写入；没有业务返回值不能造成功结果。内部 ToolOperation schema=1 增加可缺省 effect_resolution/evidence，旧记录仍能读；重新派发前清除上次证据，原核查回执保留。历史中显式保留已核实效果，不能把不可用输出解释为未执行。
6. Runtime 获得排他存储所有权后分页扫描未结束记录：原人工请求保留/检查有效期；accepted/running 转 recovery，已派发未知写入优先 reconciliation。扫描不执行用户代码或模型，也不要求当时已有原 Agent。recover 再校验原 revision/配置/engine/input/plan 与必要依赖，并先预留槽位；人工请求由 respond 处理，终态不复活，旧活跃调用不被另一推进绕过。
7. 检查点保存 pending_model_call/pending_model_attempt_count/pending_plan/pending_output。未提交响应可显式重试保存的同输入/call ID，以新 attempt 计数，模型 step 不重复计算、用量保持未完整标识；已提交计划/工具结果/最终响应直接复用。旧 engine=1 记录可从事件/response_ids 重建游标；版本或记录不兼容拒绝恢复，不猜测。已派发只读操作在原调用停止后可记录 read_interrupted，不能变成写入重试许可。
8. 新增 verification.started/completed 两种 schema_version=1 的持久事件；旧读者沿已有 UnknownDurableEvent 机制读未知类型。核查开始/结束各保存不可变 receipt 子类型，带 record_type=verification 与有前缀的独立 ID，不改变 SQLite DDL。查询结束时若 Run 已终态，只保存独立审计回执，不修改终态版本；未终态同时保存事件。交互响应/核查结果正文仍需 view_content，元数据不包含证据或结果。

## 替代方案与代价

- 将所有工具错误视作失败未执行、批准后直接重试：会多写，不采用。
- verifier 自动消费请求或无限后台查询：混合证据与控制且资源不可界定，不采用。
- 重启重跑整个模型/函数栈：会改变输入和已有副作用，不采用；保存语义边界并要求显式 recover。
- 核查审计新增 SQLite 表/记录类别：当前通用 receipt 已支持不可变事实，采用有类型标签的子记录，避免无必要的数据迁移。

额外查询/授权/事务有成本；非协作 Python Task/线程不能强杀，关闭可抛 ShutdownIncomplete。远端服务仍须给出足够强的 operation 查询证据，Drov 不宣称通用 exactly-once。只读 verifier 当前仅支持 async 回调，未开放任意同步查询池或自动幂等适配。正常独占进程重开不等于旧备份回退安全，维护/restore barrier 仍由 T-15 实施。

## 影响

API、运行/工程、架构、质量与 S-03 示例同步；schema 枚举是兼容性记录中的明确增量，无新依赖、uv.lock/DDL/品牌变化。T-09 的离线功能与 T-10 的硬终止/完成取消竞争/逐提交故障矩阵分别记证据；真实模型质量、沙箱、HTTP/维护/发布均不从此决定推断完成。

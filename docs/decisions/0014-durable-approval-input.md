# ADR-0014：保存的工具计划、审批回执与补充输入

- 日期：2026-10-14（Asia/Shanghai）
- 状态：T-09 审批/输入子范围工作决定；完整核查/显式恢复仍未验收，证据见 [T-09 验证](../validation/t09-interactions.md)
- 关联：细化 ADR-0005/0007/0009/0010/0012、C-08/09/11/12、B-01/04/07；保留历史 P4 fingerprint

## 原因

T-08 能原子执行 Memory，但所有 ASK 仍失败。接入人工响应时若重新调用模型，会改变已展示的参数，或重复执行同一计划里的旧工具。只保存 approved 布尔值也不足以绑定执行者、版本与有效期。

## 决定

1. 保留完整 model_response 与稳定 operation_ids，以检查点 pending_plan 引用待完成的原计划。响应后先按原顺序处理该计划，已有明确结果只读取，不再次派发；全部调用有结果才清除引用并进入下一模型步骤。累计模型 step/attempt、工具次数与活动时间延续；人工等待不计活动时间。新记录的可继续状态由这一游标维护，旧无游标记录仍可查询，不据本子范围承诺崩溃恢复。
2. interaction/receipt 使用已有 RunItem 与 schema_version=1，无 DDL 变化。请求保存原 operation request_hash、Agent config_hash 与类型化 request；回执不可变，保存规范响应、可信响应者和消费时 ResponseReceipt。请求消费、操作结果或 approval_grants 引用、检查点、事件与 running 在同一事务提交。事务内先验证，再预留活动槽位；任何容量/校验/提交失败均不消费请求。调度失败保留回执并转为 recovery，后续 recover 尚待实施。
3. 规范相同响应在重新检查响应者权限后返回旧回执，不受后来到期或终态影响；冲突抛 InteractionConflict。新审批/输入到期在读取/控制时以 interaction_expired 收尾。派发前从已消费请求及不可变批准回执验证原 operation/hash/config/参数/有效期；批准只满足 ASK，原用户当前业务否决和 deny 仍优先，批准人的身份不成为执行身份。原 operation 数据格式保持不变。
4. HumanInput 是 ToolDefinition 的内置 read 描述，只有 prompt 参数，结果 codec/schema 来自注册 input_type。正常权限顺序可先产生审批，再产生 input；合法 InputResponse 原子保存成原调用结果并计一次工具使用，无外部派发、无函数栈恢复。非法答案不消费，当前业务权限撤销阻止消费。临时 Agent.run 通过显式私有临时标志保持 interaction_unavailable，无 request_id；不以存储是内存就判断临时入口。
5. 内核为跨用户控制做同 tenant 的私有 Run ID 定位，拿到可信归属后用应用 Authorizer 检查 respond/read/view_content/cancel；正常数据查询仍按原 tenant/user。未经授权不能返回内容或消费请求，跨 tenant 一律不可定位；所有后续工具使用原 Run 归属。读取后再次授权，避免等待存储期间撤权仍泄露正文。
6. 本子范围只开放 read PythonTool、内置 Memory 与 HumanInput 的审批/输入。外部 write/unknown 接收前继续拒绝；verify/recover 无公共空壳。其 unknown/reconciliation、显式恢复与故障矩阵须完成 T-09 剩余范围，不能把读工具计数或 Memory 的专用事务保证视作外部写入安全证明。

## 替代方案与代价

- 批准后让模型重做计划：参数可能改变、已有调用可能重复，不采用。
- 将批准人当执行者或使用独立 approved 开关：无法约束原身份、请求和有效期，不采用。
- 暂停任意 Python 工具栈：恢复需要可执行闭包/连接状态，边界不可解释；采用注册描述与保存的原计划。
- 内存状态保存批准或先消费再排队：进程退出丢事实、容量不足留下无人推进记录；采用已有事务和活动槽位。

人工往返增加事务读取与注册复核；授权器在事务内不得重入同一 Storage，应查询自身权限数据，检查有超时。宿主仍须正确维护 revision，指纹不检查任意函数体。到期检查依赖读/控制，无独立定时通知；本子范围没有通用启动恢复扫描、外部写入或真实模型质量结论。

## 影响

公共新增 HumanInput 与 Runtime.respond/resume 的可执行审批/输入子集；使用现有 DTO/schema，无新依赖、锁文件或 SQLite DDL 改动。API、运行、架构、工程、案例、质量、安装示例和 progress 同步；T-09 保持进行中，未关闭 S-03-D、B-05 及完整 B-07。

# ADR-0009：Python 工具、权限顺序与受控派发

- 日期：2026-10-13（Asia/Shanghai）
- 状态：T-05 实施决定；只读 Python 工具与权限/记录接点已实现，完整 Agent/审批/核查仍由后续任务验收
- 关联：细化 ADR-0005/0006/0007/0008，保持 API/运行契约 v3 与首期范围；历史 P4 指纹不覆盖

## 原因

T-02 的 ToolDefinition 只有 schema 和 codec，不能执行函数。T-04 拥有任务和真实 Future，但还没有将业务授权、线程容量、派发事实及结果保存连起来。直接 asyncio.to_thread 或无限 submit 会使线程排队不可见，等待取消也不能证明调用停止。

## 决定

1. @tool(name=..., revision="1", effect="unknown", approval="policy") 返回 PythonTool，继承 ToolDefinition 并持有真实 callable；装饰只校验，不执行。同步/异步函数共用参数及结果 codec，拒绝生成器和保留的 Memory 名称。schema-only 定义仍可检查，但不能当作可执行工具；T-06 接收前必须校验可调用配置。函数体/闭包无法由指纹固定，仍须更新 revision；同一 Runtime 内替换工具对象会被拒绝。
2. PermissionPolicy 的 deny/ask/allow 是不可变名字集合，构造支持 set/frozenset/list；固定顺序为 scope/业务授权否决、deny、required approval/ask、allow、动态检查通过的 read、默认 ask。Authorizer 增加 execute_tool action 与 ToolResource（原归属、Run/Session/operation ID、工具名、规范参数）。OwnerAuthorizer 是本人访问/控制默认值，不提供 HTTP 认证。工具执行使用原运行归属，不把批准者身份替换为执行者。
3. Runtime 接收 policy/authorizer，二者配置引用只读；应用可通过同一 authorizer 对象返回当前业务授权。每次尝试、队列等待后和派发标记提交后重新检查；传给 authorizer 的参数快照每次重新解析，不能改写函数实参/下一次授权。授权异常/超时拒绝派发，不序列化异常正文。policy 不作为已保存的批准凭据，不缓存上次授权；当前规则始终生效。
4. Runtime 为实际 Python 工具创建自有 ToolPool，按全局线程数与等待队列上限预留。队列等待属于内核任务，拿到工作位置后才提交派发标记、再次授权和调用。同步工具登记原 concurrent Future；异步工具先确立 Task 引用再允许函数进入。等待取消独立于工作，未停止调用继续占槽位和资源，关闭超时保留锁；线程不可强杀。
5. 内部 ToolOperation payload schema=1 保存规范参数、请求摘要、版本、attempt 数与 ID、阶段、结果可用性和安全错误码。OperationJournal 在一个事务更新 RunItem、Run 摘要/版本、检查点和事件；tool.dispatched 同时预留 Checkpoint.data.tool_dispatch_count，达到保存的 max_tool_calls 不派发。该计数是保守的派发预算预留，提交后取消/撤权等可能使函数没有实际执行，不回退预留。已有操作仅返回已保存事实；同 ID 不同请求或并发派发拒绝，不以重复调用充当批准或恢复。
6. 本轮实际派发只允许 read。ASK 保留 prepared/approval_required，不生成虚假 request_id；write/unknown 即使 policy allow 也返回 not_executed/effect_not_supported。完整交互与未知效果路径在 T-09/T-10 接入。只读额外重试仅接受 TransientToolError，每次有新 attempt_id 并重新授权/扣预算；普通错误、超时或输出不可用不自动重试。零重试合法，默认值沿用 Limits。超时/取消不使用迟到结果，不把包装任务结束作为调用已停止的证据。
7. 工具返回值经 codec 和 UTF-8 字节上限校验；非法/超大/未预期 coroutine 输出记录 failed_known/output_unavailable，None 成功用 has_result=True 区分。内部完整记录不是授权投影；存储错误向调用者抛出 PersistenceUnavailable，不伪造已保存结果。完整模型计划、Run 终态/活动预算、临时 ASK 失败和正文权限由 T-06 连接，不在 T-05 制造执行入口。

## 替代与代价

- 让 schema 描述也带隐式执行行为会破坏 T-02 定义边界；选择显式 PythonTool，schema-only 迁入执行配置时要改用装饰器。
- 无限 ThreadPoolExecutor 队列或仅登记 asyncio 包装 Future 会丢失真实资源占用；选择有界预留与原 Future，代价是满载拒绝、等待受预算约束及无法立即结束的关闭。
- 授权一次后排队更简单，但会忽略等待期间撤权；重复当前检查更保守，应用 authorizer 必须适合重复调用，不把检查本身当作业务写入。
- 对任意异常重试可能重复执行或掩盖结果处理错误；选择显式瞬态读错误。本轮不以测试替身批准写入，也不通过 approved=True 等空入口预建交互。

定义/权限可独立于 Runtime 使用；本轮完整派发/记录验收实际复用了 T-03/T-04，任务卡前置已在 implementation-plan 同步。SQLite/RunItem 全局 schema 不变；新 operation payload 单独版本化，不将旧任意 data 猜成已执行记录或隐式迁移。无新依赖。effect=read 是应用的声明，不是沙箱或业务后端权限的替代。

## 影响与证据

[API](../planning/api-draft.md)、[运行规则](../planning/runtime-contract.md)、[架构](../planning/architecture.md)、[工程](../planning/engineering.md)、[质量](../planning/quality-plan.md) 和参考案例同步实现边界。[T-05 验证](../validation/t05-tools.md) 记录实际同步/异步/队列/撤权/存储故障与 SQLite 重开实验；不是完整审批、Agent 或真实模型质量报告。

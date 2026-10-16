# 公共 API 草案 v3

状态：P4 v3 基线；T-02 至 T-08 已实现定义/存储/生命周期、只读 Agent 闭环、授权事件/结果读取、上下文选择/清单及内置事务 Memory；T-09 已实现审批/输入与 respond/resume 子范围；T-09 已接入外部未知效果、verify/recover/启动扫描；T-10A 文件/代码沙箱公共接口与 Runtime 闭环已实现；OpenAI Responses离线适配及MCP stdio tools已实现；真实模型未验证，HTTP按ADR-0025接通，工作台按ADR-0026接入；实际验收状态见progress。T-06/07 细化见 [ADR-0010](../decisions/0010-read-only-agent-loop.md) / [ADR-0011](../decisions/0011-context-assembly.md)。更新：2026-10-15。

本文是调用方式的维护位置。状态、事务、权限、Memory 时序等详细规则引用 [行为契约](runtime-contract.md)，不在示例中另设规则。依赖方向见 [架构](architecture.md)。

## 核心概念

| 概念 | 责任 |
| --- | --- |
| `Agent[T]` | 可复用定义，包含模型、指令、工具、输出类型、Memory/上下文配置 |
| `Session` | 归属明确的持续会话，保存历史并限定一个未结束运行 |
| `Run` / `RunRef` | 一次执行及其标识，包含版本、状态和交互 |
| `Runtime` | 统一执行、任务所有权、依赖与资源管理、持久化及恢复 |
| `DrovApp` | HTTP/SSE 和本地工作台的适配与装配，不新增循环 |

持久化 Agent 必须提供 name 和 revision，并注册进 Runtime。临时调用可使用自动临时名称/revision；记录不能据此被宣传为跨进程可恢复。未指定 output_type 时，完成输出默认为字符串。

## 入口和返回类型

所有需要访问已存在记录的操作都要求可信 context；控制者权限与原运行归属分别处理。

| 入口 | 返回与行为 |
| --- | --- |
| `await agent.run(input, *, context=None, limits=None)` | `RunOutcome[T]`；临时无会话环境，使用相同内核 |
| `agent.run_sync(input, *, context=None, limits=None)` | 同上；当前线程已有事件循环则报入口使用错误 |
| `await runtime.start(agent, input, *, session_id, context, request_key=None, limits=None)` | 已持久化的 `RunRef`，不等待任务完成 |
| `await runtime.wait(run_id, *, context)` | 下一次暂停或终态的 `RunOutcome`；取消等待不取消执行 |
| `await runtime.run(agent, input, *, session_id, context, request_key=None, limits=None)` | start + wait |
| `runtime.stream(agent, input, *, session_id, context, request_key=None, limits=None)` | 异步上下文 `RunStream`：start + 事件订阅 |
| `runtime.events(run_id, *, context, after=None, include_content=False)` | 异步上下文订阅已有运行；默认元数据，不创建新运行 |
| `await runtime.respond(run_id, *, request_id, response, context)` | `ResponseReceipt`；只确认响应已接收/原子消费，不等到完成 |
| `await runtime.resume(run_id, *, request_id, response, context)` | respond + wait；重复同响应也不重新派发 |
| `await runtime.cancel(run_id, *, context)` | `CancelReceipt`；不表示副作用已撤销 |
| `await runtime.verify(run_id, *, request_id, context)` | 已注册只读 verifier 的 `ReconciliationDecision` 建议；不消费请求或推进运行，需随后 respond/resume |
| `await runtime.recover(run_id, *, context, wait=True)` | 默认等待得到 outcome；wait=False 返回 RunSnapshot，包含当前状态/暂停信息 |
| `await runtime.inspect(run_id, *, context, after=None, limit=100, include_content=False)` | `RunInspection`，默认元数据快照、分页事件/上下文索引；after 是持久化事件游标 |
| `await runtime.get_context(run_id, model_call_id, *, context, include_content=False)` | 已授权的 ContextManifest；正文需额外权限 |
| `await runtime.sessions(*, context, after=None, limit=100)` | 按可信归属筛选的 SessionPage |
| `await runtime.session(session_id, *, context, after=None, limit=100)` | 会话摘要与分页 RunRef，不默认返回所有正文 |

HTTP 映射与错误类别在行为契约第 10 节。服务启动响应和事件读取分离，因此浏览器断连不会隐式再次创建任务。

run/wait/resume/stream/recover(wait=True) 返回正文，预检 read 与 view_content；其中 start 或 respond 尚未发生时就完成权限检查。stream 默认提供正文事件，其 result() 与 wait 相同。inspect/events/get_context 的 include_content=True 同样要求两项权限，缺失则拒绝；默认元数据投影不暴露类型化输出、工具参数或 token。respond 可用于只有响应权限的审批者。详情、动态撤权及原始协议项限制见行为契约第 6 节。

verify 需要核查响应权限及 read/view_content，以原执行者当前权限进行只读查询；同一 operation 同时至多一个 verifier 调用，占用活动容量并保留资源所有权。它不接受模型传来的新查询目标，也不执行写入。查询期间原请求被其他合法响应消费时，迟到建议不能再用于推进旧请求。

T-06 已实现表中的 start/run/wait/cancel/inspect/events/stream 与 Agent.run/run_sync。T-07 已实现 get_context；T-09 子范围已实现 HumanInput、respond/resume 的 approval/input 路径，见 [ADR-0014](../decisions/0014-durable-approval-input.md)。T-09 已实现 verify/recover 与 reconciliation 响应；sessions/session按ADR-0025已接通授权分页。显式 Runtime 接收 callable Python 工具（read/write/unknown）、HumanInput 及配置 Memory 的 Agent；ASK 保存原计划并 suspended/approval。临时 Agent.run 遇到 ASK/input 仍 failed/interaction_unavailable；外部 write/unknown 按权限/审批单次派发，未知结果暂停核查。inspect.context_manifests 只返回本次事件页中 context.prepared 对应的摘要（sources=[]、content_visible=False），沿事件游标可遍历全部索引；get_context 返回完整来源清单，include_content=True 返回选入内容/预览，provider 正文始终不可见。旧完整轮次确定性选取，保护项无法装入时提前失败；清单预算/粗估包括协议和 schema，供应商已报告用量单独关联。默认查询按 context 的 tenant/user 隔离；同 tenant 跨用户查看/响应须由应用 Authorizer 分别授予，工具继续使用原用户身份。跨 tenant 无法定位。

Runtime 的有效限额从 Agent 默认与管理员上限取较严格值，显式 Limits 仅覆盖设置过的字段且不得放宽管理员上限；Agent.run 临时环境由应用配置限额。ScriptedModel/ScriptedStep 可从 drov.models 导入，用预定响应或事件/异常及输入检查构建离线序列，不调用外部模型。

## 一次性调用

`Agent.run` 不延续历史、不写持久化文件。显式启用持久化 Memory 的定义不能走这个入口，必须使用 Runtime。动态审批/补充输入返回 failed / interaction_unavailable，待授权动作不会执行，且不会生成不可用的恢复 ID。

```python
# T-06 可用入口；model、search、Answer 由应用提供。
# search 需由应用声明为只读并通过相应权限检查。
agent = Agent(
    model=model,
    instructions="根据资料回答，并注明来源。",
    tools=[search],
    output_type=Answer,
)
result = await agent.run("比较两种方案")
if result.status == "completed":
    print(result.output)
```

工具默认为 unknown effect；纯检索工具应明确声明 read。审批不可用时不能通过默认放行来让入门示例“看起来能运行”。

## 持久化与注册

T-04 可调用 `Runtime(agents=..., storage=..., dependencies=..., owned_models=..., limits=...)` 的 open/aclose 或异步上下文。`registration(name, revision)` 返回 AgentRegistration（name/revision/fingerprint_version/config_hash）；`validate_record(record)` 是对存储载入记录的版本/指纹/必要依赖检查，不推进运行。`storage` 和 `dependencies` 只在打开时可取，dependencies 映射只读；`active_slots` 展示私有内核当前预留量。Runtime 绑定一个事件循环，关闭后不能重新打开；关闭不完整可重试 aclose。下面的 run 示例已可使用注册的离线适配器及 read 工具执行，完整例子见 [离线检索](../../examples/README.md)。

Agent 的 `model_config={...}` 用于非秘密的模型配置/版本引用，`required_dependencies=[...]` 用于声明必要依赖名。注册指纹不能检测任意函数体和未声明的客户端配置，应用仍须更新 revision；同一个 Runtime 内替换模型对象也会被拒绝，防止绕过资源所有权。缺 revision、指纹/记录 schema 不兼容或必要依赖缺失均拒绝恢复接点。

未打开的 Storage 默认是 Runtime 自有描述；传入已打开实例默认借用。模型和 dependencies 中的现有客户端默认借用，`owned_models=[agent.model]` 显式转交模型所有权。`ResourceBinding.borrowed(value)` / `.owned(value, close=...)` 显式选择所有权，`.factory(create, close=...)` 在 open 时异步创建自有存储或依赖。自有对象默认调用 aclose；也可提供异步 close(value)。借用存储必须已打开，宿主负责它的最终关闭。不要在 Runtime 构造后自行改变所传对象的打开/关闭状态。

```python
# T-04 生命周期片段；model/items/create_client 由应用提供，不执行 Agent。
from drov import Agent, ResourceBinding, Runtime, SQLiteStorage

agent = Agent(
    name="assistant",
    revision="1",
    model=model,
    model_config={"model_id": "application-config-v1"},
    required_dependencies=["items", "client"],
)
async with Runtime(
    agents=[agent],
    storage=SQLiteStorage("drov.db"),
    dependencies={"items": items, "client": ResourceBinding.factory(create_client)},
) as runtime:
    registration = runtime.registration("assistant", "1")
    assert runtime.dependencies["items"] is items
```

装配/逆序清理、工厂返回前失败的责任、关闭宽限与保留锁规则见运行契约第 9 节及 ADR-0008。实际线程 Future 的登记与任务控制是私有内核接点，不作为应用公开执行接口。

```python
# T-06 可用只读入口；Agent 定义与模型适配器由应用重建。
agent = Agent(name="assistant", revision="1", model=model, tools=[search])
caller = RunContext(tenant_id="local", user_id="alice")

async with Runtime(
    agents=[agent],
    storage=SQLiteStorage("drov.db"),
) as runtime:
    result = await runtime.run(
        agent,
        "继续上次讨论",
        session_id="conversation-01",
        context=caller,
        request_key="request-001",
    )
```

Session 以 tenant_id、user_id、session_id 共同寻址。RunContext 不进入模型可编辑字段。请求重试使用相同 request_key；发起新的独立请求必须用新键或不设键。

持久化 Runtime 首期独占该存储；同会话未结束运行不会被新输入隐式覆盖。应用必须能按原 Agent revision 重建注册和依赖，才能在新进程响应旧审批或恢复。

## 流式入口

```python
# T-06 可用流式入口；以下位于已有 Runtime 的上下文中。
async with runtime.stream(
    agent,
    "生成报告",
    session_id="report-01",
    context=caller,
) as stream:
    async for event in stream:
        render(event)
    result = await stream.result()
```

RunStream 是异步上下文和异步迭代对象；result() 等待当前推进暂停或终结。提前退出迭代只解除订阅，任务仍归 Runtime 管理；随后退出 Runtime 才触发关闭协议。所有权细节见行为契约第 1、9 节。

事件包含 durable 标记。只有已提交持久化事件有 seq 和可重连游标；临时 token 不承诺补发。工具执行和权限规则不因选择流式改变。

## 结果、错误与交互响应

| 对象 | 核心字段/含义 |
| --- | --- |
| RunRef | run_id、state_version；创建已接收的引用 |
| RunSnapshot | run_id、state_version、status、reason、cancel_requested、usage、content_visible、当前操作摘要 |
| Completed[T] | status=completed，output: T，run_id、state_version、usage |
| Suspended | status=suspended，reason、pending，run_id、state_version；恢复暂停可没有人工请求 |
| Failed | status=failed，error，run_id、state_version；不回滚此前副作用 |
| Cancelled | status=cancelled，操作记录摘要，run_id、state_version |
| ResponseReceipt | run_id、request_id、已保存响应标识、state_version；不等于任务完成 |
| CancelReceipt | run_id、cancel_requested、state_version；需 inspect/wait 查看最终处理 |
| RunInspection | snapshot、events、next_cursor、has_more、context_manifests；后者为清单摘要，正文单独读取 |

只有 Completed 提供成功 output；示例不能在 suspended/failed 上读取 output。未创建运行的配置/输入错误抛出类型化异常；已创建运行的执行失败保存在 outcome。存储本身不可用时抛出 PersistenceUnavailable，不伪造已落盘状态。

所有 RunOutcome 均带 usage，失败、暂停、取消也可能有费用；未知用量不填 0。返回元数据快照时 outcome=None、content_visible=False，并非该运行没有结果。元数据 pending 仅保留请求标识/类型/期限；完整 InteractionRequest 只能在有正文权限的视图中获得。

交互响应分为 ApprovalDecision、InputResponse、ReconciliationDecision。控制输入须校验；模型不能替用户创建批准响应。API 支持在同一 run_id 上响应，但真正请求绑定在保存的 operation 与身份上。

```python
# T-09 approval/input 子范围已实现；authenticated_reviewer 来自应用授权边界。
result = await runtime.resume(
    run_id,
    request_id=request_id,
    response=ApprovalDecision(approved=True),
    context=authenticated_reviewer,
)
```

审批拒绝、请求过期、重复响应、结果核查和子请求路由的语义以行为契约为准。resume 的便利性不能绕过 respond 的校验。

## 工具与依赖

T-05 的 `from drov import tool, PermissionPolicy, OwnerAuthorizer` 已实现。@tool 返回不可变 PythonTool，继承 schema-only ToolDefinition 并持有实际 callable；参数仍按 T-02 子集校验，ToolContext 按类型注入。name 必填，revision 默认 "1"，修改行为仍须更新 revision；effect 默认 unknown、approval 默认 policy。生成器与内置 Memory 名称拒绝。Agent.tools 接受装饰后的对象；compile_tool 的结果可检查但没有执行能力。

```python
# 已实现的定义/权限检查；不调用 Agent 或模型。
from drov import PermissionPolicy, OwnerAuthorizer, RunContext, ToolContext, tool
from drov.auth import ToolResource


@tool(name="square", revision="1", effect="read")
def square(value: int, ctx: ToolContext) -> int:
    return value * value


owner = RunContext(tenant_id="local", user_id="alice")
decision = await PermissionPolicy().decide(
    square,
    actor=owner,
    resource=ToolResource(
        owner=owner, tool_name="square", operation_id="op", arguments={"value": 3}
    ),
    authorizer=OwnerAuthorizer(),
)
assert decision.decision == "allow"
```

PermissionPolicy(deny={...}, ask={...}, allow={...}) 保存不可变集合，支持 set/frozenset/list。decide 返回 PermissionDecision 的 decision/reason；业务 Authorizer 使用新增 execute_tool action 与 ToolResource 检查原归属及规范参数。默认 OwnerAuthorizer 只允许本人，跨用户查看/审批仍需宿主明确授权。Runtime 接收 policy/authorizer，配置引用只读，原 authorizer 对象可反映动态业务权限；当前权限会在尝试、队列等待后、派发标记提交后再次检查，allow 不覆盖否决或 required approval。

外部 Python 派发已支持 read/write/unknown；后两者单次派发，派发后不明结果为 unknown，私有派发器 ASK 返回 prepared；T-09 的显式 Runtime 持久化绑定原调用的 ApprovalRequest，批准/拒绝后处理保存的原计划。临时 Agent.run 的 ASK 仍 failed/interaction_unavailable；T-09 已接入外部写入/核查/显式恢复，见 [ADR-0015](../decisions/0015-external-effects-recovery.md)。结果必须经 codec 和大小校验；非法输出不当成功，None 用内部 has_result 区分可用结果。只读瞬态错误用 `drov.errors.TransientToolError`，其他错误/超时/非法结果不自动重试。排队/线程与原子记录规则见运行契约第 4 节和 ADR-0009。


```python
# API 设计片段：依赖实例由 Runtime 的应用配置提供。
@tool(name="update_item", effect="write", approval="required")
async def update_item(item_id: str, value: str, ctx: ToolContext) -> UpdateResult:
    return await ctx.deps["items"].update(
        item_id=item_id,
        value=value,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        request_id=ctx.operation_id,
    )
```

ToolContext 按类型注入，不放进模型 schema；传递 request_id 不会自动赋予业务接口幂等保证。应用必须声明并验证后端语义，默认遇到未知写入结果暂停核查。

Runtime 接受 dependencies 映射作为简单依赖入口，也可在后续适配受控解析器；映射中的现有客户端默认为借用。恢复时应用重新提供它们，不能反序列化旧连接。

PermissionPolicy 初期提供 deny、ask、allow 的明确工具规则；高优先级的作用域校验、deny 和 required approval 不会被 allow 覆盖。补充输入通过内置 HumanInput 工具描述触发，详见下面的字段约定；不将普通工具返回文本猜成控制指令，也不恢复任意工具函数栈。

### HumanInput（T-09 已实现子范围）

`from drov import HumanInput`；构造 `HumanInput(name="ask_item", input_type=ItemChoice, revision="1")`，input_type 使用既有结果 schema 子集。工具只接受 prompt: str，无宿主 callable；InputResponse.value 经过注册 codec 校验成为原调用的结果。默认 read，应用 ask 会先生成审批，deny/当前业务否决仍生效。非法值/多余字段不消费请求，用户可修正；输入与批准不能混用。跨进程重建相同 Agent revision/schema 后可响应保存的请求，示例见 [审批/输入离线检查](../../examples/interactions_offline.py)。

显式 Runtime 支持内存或 SQLite 存储；跨进程事实需要 SQLite。respond/resume 消费人工请求，recover 处理原恢复暂停，不绕过审批/输入/核查。累计预算、回执和到期语义以运行契约为准。

### 外部写入、verify 和 recover（T-09）

`@tool(..., verifier=verify_item, verifier_revision="1")` 可显式绑定异步只读回调 `async def verify_item(ctx: ToolContext) -> ReconciliationDecision`。ctx 固定为原用户/run/operation 及当前应用 dependencies；verifier 不接收模型提供的新目标。没有回调时 verify 抛 ConfigurationError；改变核查实现/服务契约须更新对应 revision。普通 Python 工具由宿主执行，effect/verifier 声明不提供隔离安全证明。

write/unknown 没有自动重试；超时、异常和不可用输出暂停核查。verify 不消费请求或继续 Agent；随后明确 respond/resume 提交证据。applied 正常继续须有原工具 codec 可验证的 result；取消/预算收尾可省略 result。not_applied 仅在原调用停止后重走当前权限/审批；unresolved 换新 request_id。证据非空和 schema 合法不证明业务真实性，宿主须提供可靠操作查询或获授权核查人。

`recover(..., wait=False)` 返回无正文 RunSnapshot；默认 wait=True 要求正文权限并等待下个暂停/终态。启动扫描不执行外部动作；recover 不消费人工请求，也不复活终态。未提交模型响应只允许按原保存输入与剩余次数重试，可能有新输出/用量；已提交模型计划、工具结果与最终响应不重做。详见 [核查/恢复验证](../validation/t09-reconciliation-recovery.md)。

## 内置 Memory 与上下文

`Memory(retrieval="required", writeback="explicit")` 是资源/策略描述，使用当前 Runtime 的事务存储；不是打开中的连接。可配置 best_effort 读取与期限，默认值及最近/键/子串检索方式见 [工程方案](engineering.md)。

它注册 memory.remember、memory.recall、memory.forget 三个逻辑工具名。模型供应商若限制工具名格式，适配器必须建立无冲突的导出名映射并记录映射；不能改变权限系统使用的逻辑身份。

```python
# T-08 已实现：explicit 指通过明确工具写入。
agent = Agent(
    name="personal-assistant",
    revision="1",
    model=model,
    memory=Memory(retrieval="required", writeback="explicit"),
)
policy = PermissionPolicy(allow={"memory.remember", "memory.forget"})
```

Memory 工具仍受用户作用域与应用权限限制；allow 只在该配置启用时生效。工具成功意味着本地写入和操作结果共同提交；任务 completed 不保证模型调用了保存工具。通过 inspection 的 memory.changed 事件及测试数据验证记忆行为。

当前 ContextPolicy 字段为 memory_fraction/memory_max_tokens；输入预算与工具预览大小使用 Limits.max_input_tokens/output_reserve_tokens/tool_preview_bytes。Memory 未启用时不占用子预算；T-08 已接入实际检索及来源版本。每次新的模型输入都有 ContextManifest；其选入顺序、required/best_effort 时序、删除与历史保留边界唯一规定在行为契约第 7 节。

## 应用服务与资源

```python
# T-13 实际装配：映射键与 Agent.name 一致；认证由应用提供。
service_agent = Agent(name="assistant", revision="1", model=model, tools=[search])
app = DrovApp(
    agents={"assistant": service_agent},
    storage=SQLiteStorage("drov.db"),
    auth=application_auth,
)
```

同一 Agent 定义也可以先由显式 Runtime 驱动，用相同配置检查 SDK 与 HTTP 行为。

DrovApp 在服务生命周期内管理其创建的 Runtime；也允许接收外部管理的 runtime，二者装配方式互斥。借用模式不关闭外部 Runtime，宿主负责最终关闭。工作台通过 HTTP 调用，没有独立审批规则。

本地演示与应用认证是明确模式；不默认信任任意请求头给出的 user_id。Runtime 关闭期间停止接收，持久化已有暂停，处理活动运行并释放自有资源；无法安全结束时报告 ShutdownIncomplete。

## P1 问题处理记录

| ID | P2 结论 | 规范位置/剩余工作 |
| --- | --- | --- |
| Q-API-01 | 临时入口不提供持久化交互；动态 ASK 失败而不执行待授权操作 | 行为契约第 1 节，B-01 |
| Q-API-02 | 明确状态机，步骤检查点与关键事件原子提交，工具顺序执行 | 第 2 至 4 节，B-02/03/05 |
| Q-API-03 | 等待/订阅与执行寿命分离；取消不确定写入需核查 | 第 8、9 节，B-05/06/07/12 |
| Q-API-04 | 归属固定，响应者与执行者分开；自有和借用资源区分 | 第 5、6、9 节，架构数据表 |
| Q-API-05 | 固定上下文清单、限时检索、内置 Memory 工具事务提交 | 第 7 节，ADR-0003 |
| Q-API-06 | 异步核心；仅 Agent.run_sync；已有循环明确报错 | 第 1 节，B-13 |
| Q-API-07 | 同 Runtime 顺序子运行、独立作用域、关联恢复和交互 | P3 决定延后到 v0.2 候选，首期无委派 API；B-14 后续验证 |
| Q-API-08 | Python/适配器/MCP/包布局/默认值、首期字段与 schema 子集已经 P4 复核 | 工程方案、下文字段约定；T-01 解析实际依赖锁，T-02 生成类型/schema |

这些工作决定已纳入 P4 实施基线。任何语义改变必须同步受影响的架构、行为契约、能力表及参考应用，并记录 ADR；仅改示例绕开规则不算解决问题。

## 首期公共字段约定

T-02 已将下列 DTO/配置与模型协议落成 Python 类型，并生成 [schema 文档集合](../reference/schema-v1.json)；T-13 的实际路由生成 [OpenAPI](../reference/openapi-v1.json)，实施验收状态见progress。实际模块导出见 [定义参考](../reference/README.md)。数据值不允许任意 Python 对象；类型子集与严格校验见 [工程方案](engineering.md) / [ADR-0006](../decisions/0006-python-contracts.md)。运行入口按上文实施状态使用，含 Memory/审批/服务的片段仍为设计。

### 公共配置与标识

- 文本 input 为非空 str；ID 为非空不含控制字符的字符串。用户提供的 tenant_id/user_id/session_id/name/revision/request_key 最多 128 字符，生成的 run/request/operation/model_call ID 是不透明 UUID 字符串，不编码用户名或密钥。
- RunContext 的公共归属字段为 tenant_id、user_id；权限/依赖来自可信宿主，不能从模型或 HTTP body 覆写。Agent.run 未传 context 时只用本次临时身份，无法访问持久化 scope。
- Agent：name/revision 在持久化注册时必填，临时定义可都省略；两者须成对提供。T-02 model 接收 ModelAdapter 实例，集成/资源描述由后续任务装配；tools 接受 ToolDefinition（仅 schema）或 @tool 产生的 PythonTool；进入执行配置时必须具备真实 callable。instructions 默认空串、tools 默认空集合、output_type 默认 str，memory 默认 None，context_policy/limits 使用工程默认值。相同对象可在各入口复用，但同一有状态客户端的所有权不能隐式转移。
- Runtime：agents、storage、policy、authorizer、dependencies、limits；dependencies 默认空映射、policy 默认保守规则、默认 authorizer 只允许本人记录/控制。跨用户 reviewer 必须由应用 authorizer 明确允许。资源只在进入 Runtime 时打开。
- ModelCapabilities：text、tools、structured_output、streaming 为 bool，context_window 为正整数或 None；未知能力不得默认为支持。OpenAIResponses 显式传 model 与 capabilities，密钥仅环境/借用客户端配置，不能作为可持久化 Agent 字段。
- T-11 的 `drov.models.openai.OpenAIResponses(*, model, capabilities, client=None, max_output_tokens=2048)` 已有离线适配；client 为借用 AsyncOpenAI，自有客户端首次 generate 才创建，需由 Runtime owned_models 或宿主 aclose 关闭。可选适配器 hook `validate_configuration(*, tools, output_schema)` 在 Agent 构造/注册前校验；`registration_config` 仅声明可入指纹的公开配置，旧适配器无需实现。TransientModelError 可附 retry_after_seconds，ModelProviderError 表示供应商确定拒绝/失败。版本/历史配对和限制见 [ADR-0022](../decisions/0022-openai-responses-adapter.md)；未知模型能力/窗口仍需显式声明，不据离线成功宣称某个真实模型已支持。
- T-16增加 `drov.models.deepseek.DeepSeekResponses(*, model, capabilities, client, max_output_tokens=2048, reasoning_effort="none")`，复用openai extra，client必须由宿主显式配置并借用；effort仅none/low/high/max，公开配置进入原指纹。私有明文历史包络与OpenAI加密包络互不转换，明文推理不作为回答/流delta；完整明文为续接依据，额外encrypted_content仅允许字符串/null且重放时移除，不能用它代替明文；不发送DeepSeek忽略的选项，不启用托管工具。权限、工具顺序、原子提交及本地上下文预算不变；协议与代价见[ADR-0033](../decisions/0033-deepseek-responses-and-evaluation.md)，实际模型成绩只见评测记录。
- Responses的文本输入边界将非字符串规范消息（包括自动Memory注入的user数据）编码为canonical JSON文本，保留role/marker和来源值；不将记忆当system指令。这个组合缺口在T-16实际复现并修复，原记录/预算/版本规则不变。
- Limits 字段对应工程默认表；时间/大小/容量/步骤限额为正数，模型/只读重试次数为非负整数（0 表示不重试），v0.1 write_tool_retries 只允许 0，只有字段明确可选时才允许 None。NaN/Infinity、非法类型、超出管理员上限的值拒绝。服务端 `limits` 只收紧，不允许启用后端未配置的能力。

### 状态与数据对象

| 对象 | 首期字段及约束 |
| --- | --- |
| RunRef | run_id: str，state_version: int（从 1 起） |
| RunSnapshot | RunRef 字段 + status 六状态枚举、reason: approval/input/reconciliation/recovery 或 None、cancel_requested: bool、agent_name/revision、session_id、created_at/updated_at、pending、operations、outcome 或 None、recoverable: bool、usage、content_visible: bool；时间为 UTC RFC3339 |
| RunOutcome | 以 status 区分 Completed/Suspended/Failed/Cancelled，均含 run_id/state_version/usage；Completed 含 output，Suspended 含 reason/pending，Failed 含 error/operations，Cancelled 含 operations；非完成态不带成功 output |
| Usage | input_tokens/output_tokens/cached_input_tokens/total_tokens 各为非负 int 或 None，cost_amount: 十进制字符串或 None、currency 或 None、complete: bool；未知不能填 0 |
| RunError | code、message、stage、retryable: bool、operation_id 或 None；message 不含密钥/未授权正文，retryable 不授予写入重做权 |
| InteractionRequest | request_id、kind、run_id、operation_id、created_at/expires_at、prompt、schema_version、参数摘要/输入 schema/核查说明按 kind 区分；至多一个 active；核查 expires_at 仅为提醒期限 |
| InteractionSummary | request_id、kind、run_id、operation_id、created_at/expires_at、schema_version；不带 prompt/参数/schema 正文。RunSnapshot.pending 在 content_visible=False 时仅含此类型，有正文时含 InteractionRequest；Suspended.pending 为完整请求 |
| ResponseReceipt | run_id、request_id、response_id、state_version、accepted_at；重复相同响应返回同一值，不生成新 receipt |
| CancelReceipt | run_id、cancel_requested、state_version、status；状态仅为请求时快照 |
| DurableEvent | schema_version=1、run_id、seq（从 1 递增）、state_version、type、occurred_at、durable=True、data、content_visible: bool；data 为授权后的结构化投影，不能直接序列化底层完整记录 |
| TransientEvent | run_id、type=token.delta/stream.gap/stream.error、durable=False、data；无 durable seq，不给可补读游标；stream.error 只含安全错误码/摘要 |
| ContextManifest | schema_version、model_call_id、run_id、agent revision、input_ref/input_hash、budget/estimator/estimated_tokens、reported_usage、sources、retrieval_status、content_visible: bool；source 含 id/version/kind、included、reason、transform、估算用量；正文按权限另带，原始供应商协议项不可展示 |
| RunInspection | snapshot、events: list[DurableEvent]、next_cursor: str 或 None、has_more: bool、context_manifests: 本页事件涉及的清单摘要；不通过一次请求返回无限事件/清单 |
| SessionPage / SessionView | Page 含 items/next_cursor/has_more；item 为 session_id/agent_name/active_run_id/created_at/updated_at；View 加分页 runs 和对应游标，不隐式删改会话 |

分页事件按 seq 升序；inspect.after 与 events.after 均为 run_id:seq，None 表示从头，seq=0 表示首条之前，其他值不得超过当前最后序号；跨 Run/非法游标返回 invalid_cursor，历史已清理返回 cursor_expired。此游标不是授权凭据，每次仍校验当前权限。next_cursor 为本页末条事件游标，空页为 None；has_more 仅描述该次读取快照中是否还有事件。

Session 列表按 created_at、session_id 降序做 keyset，会话 Run 按 created_at、run_id 降序；这两类游标使用绑定 scope/查询且有完整性校验的 opaque 值，篡改/跨查询返回 invalid_cursor。inspect 与 get_context 都先校验归属。页数/正文访问限制不改变恢复所用数据。

事件类型覆盖 run 的接收/运行/暂停/终态/取消请求、模型尝试、工具意图/派发/结果/拒绝/未知、交互请求/响应/过期、context.prepared、memory.changed 和 recovery.required。T-02 的 DurableEventType 已建立命名枚举及生成 schema；parse_event 将未知名称保留为 UnknownDurableEvent，只用于只读提示，不授予执行语义。真实事件载荷和授权投影在对应内核任务验证。memory.changed 的 action/key/version 保持 P2 约定。

### 交互和 Memory 工具

| 输入 | 字段/处理 |
| --- | --- |
| ApprovalDecision | kind=approval（SDK 可用默认判别值），approved: bool，comment: str 或 None；不能携带新工具参数 |
| InputResponse | kind=input，value: JSON 值；必须通过原请求保存的 input_type schema，错误不消费请求 |
| ReconciliationDecision | kind=reconciliation，resolution=applied/not_applied/unresolved，evidence: 非空说明；applied 正常继续时须有通过原返回 schema 校验的 result；取消/运行限额收尾可省略 result，以核实效果的证据结束；not_applied/unresolved 不带成功结果；省略与合法 JSON null 由字段是否存在区分 |
| HumanInput 描述 | HumanInput(name, input_type)，input_type 为 str 或对象 schema；生成一个内置工具，模型调用参数只有 prompt: str。Runtime 保存原工具 operation 与所需 schema，合法 InputResponse 成为该调用的结果；不再调用一个外部函数 |
| memory.remember | key/content: str，expected_version: int；新建使用 0，更新先读取版本；key 非空，expected_version 非负且不接受 bool，key/content 的规范 JSON 合计受 max_memory_bytes 限制。返回 key/version |
| memory.recall | key/query: str 或 None（不能同时非空），limit: int（1–50，默认 5）；返回带 key/content/version/source/deleted 的列表，始终限定 scope；精确 key 查询可返回 content=None 的删除墓碑，其他模式排除墓碑 |
| memory.forget | key: str，expected_version: int；不存在、已删除或版本不匹配返回 memory_conflict；返回 key/version/deleted；重复 operation 返回既有结果，不重新删写 |

HumanInput 是可选工具描述，不自动注入每个 Agent。它是 Runtime 内置交互处理器，无外部写入，以 read 注册并遵守完整工具权限顺序；默认直接产生 input 请求，应用若另设 deny/ask 则仍生效。模型不提供用户回答。首期不支持任意工具返回一个未知控制对象就中断 Python 栈。S-03-E 的 operator 变体增加 HumanInput(name="ask_item", input_type=ItemChoice)，收到合法 item_id 后模型继续提出写入工具调用，原审批规则仍生效。

Memory 模型导出名映射、JSON schema 可空/默认转换遵守工程方案。输出 dict 仅是工具返回值，不等于可以用任意 dict 作为严格结构化最终输出类型。

### HTTP 请求与错误

POST /v1/runs 的基础 JSON 字段为 agent_name、revision、session_id、input，以及可选 request_key/limits；artifact_ids/upload_ids 扩展见下文文件接口；服务从已注册定义选择 Agent，拒绝上传工具代码/模型凭据/Agent 定义。缺失 session_id 不自动创建匿名持久会话。响应与事件读取分离。

POST responses 的字段为 request_id、response（上述判别 union）；POST verify 只收 request_id 并执行已注册的只读 verifier；cancel/recover 不接受新执行身份或工具参数。会话由首次 start 原子创建；GET /v1/sessions 与 GET /v1/sessions/{session_id} 提供分页查看；GET /v1/runs/{id}/contexts/{model_call_id} 提供授权的清单查看。维护清理/迁移不开放普通 HTTP 路由。

HTTP 错误形状为 `{error: {code, message, request_id}}`：未认证 401；无权读取统一 404；已知资源的控制权不足 403；静态格式 400/422；会话/版本/响应冲突 409；已清理内容或失效历史游标 410；容量不足 429；存储暂不可用 503。执行失败仍通过已存在 Run 的 outcome 返回。SSE 头已发出后的错误用非 durable 控制错误结束连接，不能再改 HTTP 状态或伪造持久事件。

内容清理后的 request_key、数据放弃和旧备份 barrier 以 [维护策略](release-policy.md) / 行为契约为准；恢复旧数据不是允许旧操作再次执行。

## 首版文件与代码沙箱接口（T-10A）

公共沙箱入口、审批/执行/销毁/输出发布已接通，决定见 [ADR-0020](../decisions/0020-runtime-sandbox-execution.md)，可运行离线示例见 [sandbox_offline.py](../../examples/sandbox_offline.py)。模型为确定性 fixture，真实模型质量仍未验证；T-10B 故障/隔离矩阵另验收。

本节按 [ADR-0016](../decisions/0016-sandbox-artifacts-and-backends.md) 扩展 ADR-0013。T-10A 第二批已实现值类型/私有字节层、Runtime store 装配、start/run/stream 文件接收与 Run 授予/去重、ready 输入元数据、get/read_artifact 动态授权、上下文文件描述，见 [ADR-0018](../decisions/0018-artifact-input-admission.md) 和 [可运行文件通道示例](../../examples/artifact_inputs_offline.py)。私有 stage 仍不是 ready；输入接收不执行代码。下表入口、受保护输入与输出导出/销毁/原工具结果共同发布已实现。执行/恢复规则唯一在运行契约第 13 节，默认、镜像与支持矩阵唯一在工程方案。

| 已实现入口/对象 | 字段与责任 |
| --- | --- |
| drov.sandbox.PythonSandbox | backend: 宿主 dependency 名称；逻辑工具固定 sandbox.run_python，revision 随内置版本固定；approval 默认 policy、可设 required；effect 固定 write；不接受普通 callable 或任意后端 flags |
| drov.sandbox.DockerSandbox | image: 必填已预装 repository@sha256（manifest）或显式 sha256:image-config-id（本地构建，ADR-0017）；拒绝 tag；limits: SandboxLimits；宿主显式本地连接引用/可信 CLI；docker: 必填绝对可信 CLI 路径；host: 必填本地 Unix socket；构造不连接，Runtime 装配时预检，ResourceBinding 管理所有权 |
| drov.sandbox.SandboxLimits | execution_seconds、control_seconds、memory_bytes、cpus、pids、workspace_bytes、max_code_bytes、max_output_bytes、concurrency；严格有限正值；Docker 专属配置不作为云能力的默认保证 |
| drov.artifacts.ArtifactInput | name: 单个安全文件名；data: bytes；media_type: str（默认 application/octet-stream）；宿主 SDK 提交有界字节，框架不接受主机路径/URL，不解压档案；不是模型工具参数 |
| ArtifactRef / ArtifactInfo | Ref：artifact_id（内核生成不透明 ID）、sha256、size_bytes、media_type；Info 另有 schema_version、来源 Run/operation、状态与正文可见标记；name 属正文，不出现在默认元数据投影；引用不可变，不包含主机/容器路径、云 URL 或凭据 |
| drov.artifacts.LocalArtifacts | directory: 宿主私有目录；limits: ArtifactLimits；通过 Runtime(artifacts=ResourceBinding.owned(store)) 显式装配，字节与 SQLite 记录配对；构造/导入不读写，open 时核对目录、权限和存储绑定 |
| ArtifactLimits | max_input_files、max_input_bytes、max_output_files、max_artifact_bytes、max_export_bytes、max_owner_bytes、max_store_bytes；有界输入/单文件/总输出/持久容量，不与 stdout 上限混用；均由宿主配置 |
| runtime.start/run/stream(..., artifacts=()) | 新增默认空的宿主参数 Sequence[ArtifactInput 或 ArtifactRef]；接收时保存不可变输入并将引用明确授予本 Run；旧输出作为新输入须再次授权并保留其原内容/hash，不继承旧容器状态；相同 request_key 的规范输入包含有序文件名/字节 hash/类型或原引用 |
| runtime.get_artifact(artifact_id, *, context, include_content=False) | 返回授权 ArtifactInfo；正文模式可见逻辑文件名；不返回字节。当前 read/view_content 动态复核；知道 ID 不代表有权限 |
| runtime.read_artifact(ref_or_id, *, context) | 已实现：接受 ID 或 ArtifactRef（含 ArtifactInfo），返回有界 bytes（read + view_content）；传 Ref 额外核对其 hash/大小/类型；读前后复核，缺失/损坏提交 unavailable，不返回成功内容或重算 |
| sandbox.run_python 参数 | code: 非空 str；inputs: list[str]（Artifact ID，默认 []）；outputs: list[str]（/work/outputs 下相对文件名，默认 []）；拒绝额外字段。输入只能选本 Run 已获准引用，模型不能提交归属/hash/主机路径或改变 limits/后端 |
| SandboxResult | execution_key、exit_code: int 或 None、termination: exited/timeout/output_limit/resource_limit/cancelled、有界 stdout/stderr、两种 truncated 标记；artifact_status: not_requested/complete/rejected、artifacts: list[ArtifactRef]、artifact_error: None 或 {code: str}；字节不进工具 JSON。清理不明不能构造成功结果 |

模型只看到本 Run 获准的输入描述（ID、逻辑名、hash/大小/类型和固定输入位置 /work/inputs/<artifact_id>/<name>），不能枚举存储。审批展示原代码、所选输入与输出声明；批准绑定服务器解析的不可变清单，不相信模型填写的元数据。没有 artifacts 参数的旧调用保持原语义；临时 Agent.run/run_sync、内存 Storage 或缺配套 ArtifactStore 的文件/沙箱配置接收前 ConfigurationError。Artifact Authorizer 资源使用原 tenant/user/Agent 与来源 Run/operation、artifact_id；新输入授权也核对目标 Run，复用 read/view_content，工具派发仍检查 execute_tool。

```python
# 使用片段：需要真实 ModelAdapter 和已预装镜像/可信 CLI/本地 socket。
from drov import Agent, PermissionPolicy, ResourceBinding, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.sandbox import DockerSandbox, PythonSandbox, SandboxLimits


async def file_analysis_example(model, analysis_image_digest, docker_cli, docker_host):
    agent = Agent(
        name="analyst",
        revision="1",
        model=model,
        instructions="读取获准 CSV，生成 Python 分析，输出 summary.csv 和 chart.png；依据实际结果回答。",
        tools=[PythonSandbox(backend="code")],
        required_dependencies=["code"],
    )
    backend = DockerSandbox(
        image=analysis_image_digest, limits=SandboxLimits(), docker=docker_cli, host=docker_host
    )
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(".drov/file-example.db"),
        artifacts=ResourceBinding.owned(LocalArtifacts(directory=".drov/file-example-artifacts")),
        dependencies={"code": ResourceBinding.owned(backend)},
        policy=PermissionPolicy(allow={"sandbox.run_python"}),
    ) as runtime:
        return await runtime.run(
            agent,
            "分析本次 CSV 的合计/均值，交付汇总表和图表；给出工具实际返回的产物 ID。",
            artifacts=[
                ArtifactInput(
                    name="numbers.csv", data=b"value\n12\n18\n30\n", media_type="text/csv"
                )
            ],
            session_id="analysis-01",
            context=RunContext(tenant_id="local", user_id="alice"),
        )
```

文件接收 reserve/stage 的 OS I/O 失败在 Runtime 入口报告 PersistenceUnavailable；接收事务未成功时不返回 RunRef 或 ready 输入，不自动覆盖/删除已开始的私有暂存。

实际工具计划须选择该输入 ID，并声明 outputs=["summary.csv", "chart.png"]；应用从 SandboxResult 的真实引用调用 read_artifact，不能相信模型自述的路径/ID。返回引用保持规范 outputs 的顺序；ArtifactInfo.name 为文件名末段，完整相对输出路径只留在有正文权限的操作清单中，不成为宿主路径。所有声明文件通过校验并可持久读取才是 artifact_status=complete；缺失/非法/超限/导出不可用时 artifacts=[]、artifact_status=rejected 并保留真实代码退出事实。非正常退出/取消不发布部分文件；清理/提交未知按运行契约核查，不能以 rejected 掩盖未知资源。

T-13 的 HTTP 同一入口扩展：POST /v1/runs 新增可选 artifact_ids，仅引用本身份/Agent 可授权的 ready 输入；POST /v1/artifacts 以有界流上传单文件并明确 agent_name/revision/session_id 目标，得到短期暂存 upload ID（不是 ready 引用），只可由同作用域 start 原子消费。上传控制与总量沿 ArtifactLimits，未消费暂存按维护规则回收。GET /v1/artifacts/{id} 返回相同元数据；GET /v1/artifacts/{id}/content 授权后流式附件下载，不接受宿主路径/后端 URL。JSON Run 输入可以使用 upload_ids，不能上传代码工具/配置或把 bytes 隐式编码成模型输入；HTTP 上传是 T-13 的传输适配，不在 T-10A 预建服务。权限/错误码沿现有 HTTP 约定，内容不可用 410，猜 ID 无权读取统一 404。

`from drov.integrations.e2b import E2BSandbox, E2BLimits` 提供可选实验性真实后端。构造参数为 `template_id`、不可变 `build_id`、非秘密 `credential_ref`、宿主 `resolve_credential(ref) -> str`、项目声明 `region`、显式 `cloud_data_policy="allow"`、独立 `budget_id`、`max_cost_micro_usd`、`rate_micro_usd_per_second`，以及可选 `limits`。没有自动读取环境/配置文件；构造无 I/O，open 只读固定 build。`require_region_attestation=True` 因 SDK 不支持区域证明而前置拒绝。E2BLimits 与 Docker SandboxLimits 分开，实际 VM/工作负载/文件/时间与预算语义见工程第10节和 ADR-0023；复用原 PythonSandbox/Artifact DTO/默认审批，不新增模型控制字段。E2B 显式 Runtime 绑定启用 sandbox_schema=2，core-only 可授权读取销毁后产物，旧 SDK 拒绝该扩展版本。实际任务验收状态见 progress。

显式 allow 只授权已配置的代码工具；业务写入仍单独审批。代码生成/消费结果沿现有 ModelAdapter，没有自动代写/修复/执行循环。SandboxLimits/Result 已加入生成 schema；HTTP DTO在T-13随实际接口加入；E2BLimits 已加入生成 schema。

当前输出集合的 basename 也须唯一；PNG 验证接受有界非交错静态 profile，限制/代价见工程第 9 节与 ADR-0020。核查中的沙箱人工 response 不替代资源/结果事实；使用 recover 查询/销毁并恢复原可靠暂存，verify 仅只读。


E2B的执行能力校验与清理身份核对分别处理；坏控制配置不执行代码，但归属可证的原资源仍可销毁。该修正不增加工具参数、调用次数或可下载成功产物。


## MCP stdio tools（T-12）

`from drov.integrations.mcp import MCPStdio, MCPTool, MCPToolPolicy`；安装 `drov[mcp]`。构造仅保存宿主配置，无启动/网络。完整可运行示例见 [mcp_stdio_offline.py](../../examples/mcp_stdio_offline.py)。调用约定：

```python
server = MCPStdio(
    namespace="local",
    revision="1",
    command=absolute_executable,
    args=[absolute_server_script],
    env={},
    inherit_env=(),
)
tools = await server.discover()  # 临时预检进程发现并关闭；不调用业务工具
agent = Agent(name="assistant", revision="1", model=model, tools=tools)
runtime = Runtime(
    agents=[agent],
    storage=SQLiteStorage("runs.db"),
    dependencies={server.dependency: ResourceBinding.owned(server)},
)
# async with runtime: ...；普通run/stream/respond/resume沿原接口
```

`discover(policies={remote_name: MCPToolPolicy(effect="read", approval="policy")})` 只接受应用显式策略；未映射工具默认 unknown/policy，通常持久审批。readOnlyHint/idempotentHint 不授予权限。`tools` 是固定 tuple，每个定义的 `remote_name` 对应原名，`name` 是含 namespace 与远端名hash的Drov逻辑名，`revision` 为宿主服务revision。可从tuple选择子集注册；模型只能使用注册的逻辑名。`dependency` 是 `mcp.<namespace>`；owned/factory 自动打开未打开的连接并关闭，borrowed 需先 await server.open，由宿主最终 aclose。构造/发现/打开分别有明确边界；discover/open 单次使用，服务变化用新对象/新revision。临时 Agent.run 不含所需dependency，须使用显式Runtime。

`command`/`cwd` 为绝对路径，`args` 为字符串列表；不从模型文本启动命令或shell。`env` 显式给值、`inherit_env` 显式选择额外宿主变量名；默认仅继承固定SDK的OS白名单（包括Windows必须的SYSTEMROOT），完整名单见ADR-0024。其余宿主变量不继承，不默认传递模型Key；可由env显式覆盖OS默认项。环境值不进入指纹，服务/环境语义变化需revision；秘密仅由宿主安全配置，不能写进args、工具描述或Git。stdio服务本身与启动行为是可信宿主程序，不是代码沙箱。

支持固定分页发现、SDK initialize握手版本（本地实测2025-11-25）、闭合JSON Schema输入子集。支持有限标量/enum/对象/数组/nullable/已有数值与长度约束；未写additionalProperties时收紧为false，`$ref/$defs`、开放字典及其他关键字明确拒绝。可选字段缺省仍缺省，default不注入；严格JSON边界拒绝重复键/非有限值，之后按2020-12校验，不强转Python类型。服务outputSchema由SDK校验。结果为 `{"content": [{"type": "text", "text": "..."}], "structuredContent": object_or_null}`；非text内容/非对象structuredContent不可用，不转换为Artifact或抓取资源。更广schema、媒体结果与2026现代协议尚不支持。

每次打开与调用前复核完整发现快照；变更拒绝，不替换原审批或重发。每个SDK原始tools/call只发一次，无自动input-required回合、sampling/roots/elicitation或任务执行；task-required定义在发现时拒绝。断连/工具错误/无效结果/超时和取消关闭整个连接，不自动重连。未知写入沿原reconciliation处理，不因SDK取消确认远端效果撤销。关闭尚未结束时报ShutdownIncomplete并保留所有权，可再次aclose等待原清理；细节见 [运行契约](runtime-contract.md) 与 [ADR-0024](../decisions/0024-mcp-stdio-tools.md)。


## T-13 HTTP 实际接口

`from drov import DrovApp` / `from drov.server import DrovApp, ServerLimits`；安装server extra后构造ASGI对象。`agents`映射及storage/artifacts/policy/authorizer/dependencies/limits由宿主装配，或只传已打开的`runtime`借用。认证二选一：`local=True`（默认host=127.0.0.1/port=8000，本次lifespan token由宿主读`local_token`）或`auth=AuthAdapter`。非本地host还要求显式Authorizer、精确allowed_hosts/allowed_origins；两个列表为同源authority/origin，不含通配符。`await app.serve()`使用固定单worker；可调用ASGI对象供支持lifespan的宿主使用。不得从请求改变绑定/资源/策略。装配模式的模型仍沿Runtime默认借用规则，由宿主关闭；需Runtime拥有模型时，先以owned_models建立显式Runtime，再将其借给DrovApp并由宿主结束该Runtime。

所有数据接口使用`Authorization: Bearer ...`，包括`GET /openapi.json`；固定工作台登录/静态资产例外见ADR-0026；身份不从body或任意用户头读取。`GET /v1/runs/{id}`返回`RunInspection`，`include_content=false`默认隐藏正文。`POST cancel/recover` body是`{}`，recover固定wait=False；终态recover按内核拒绝409。`POST responses`返回ResponseReceipt，verify不消费请求。分页limit/after、上下文正文与内核接口同义；新增`Runtime.sessions(*,context,after=None,limit=100)`、`Runtime.session(session_id,*,context,after=None,limit=100)`与HTTP共用授权分页。Session游标绑定原身份/查询和存储secret，不用于授权。

SSE支持`Last-Event-ID`或`after`，同时提供必须一致。durable帧含id/event/data；token.delta/gap/error无id。头前非法或超前游标400；头后错误是无id的stream.error。关闭HTTP等待/订阅不取消Run，显式cancel走原内核。实际正文/事件上限由Runtime/ServerLimits控制。

`POST /v1/artifacts`使用`Content-Type: application/octet-stream`与明确`Content-Length`；query为`UploadTarget`的agent_name/revision/session_id/name/media_type。返回201 `UploadReceipt(upload_id,expires_at)`，不是可读ArtifactRef。Run JSON的upload_ids按顺序跟在artifact_ids后；组合输入重名/重复ID拒绝，容量/数量仍受ArtifactLimits；同request_key/相同声明复用RunRef，已消费ID另开接收冲突409。上传未完整409、已过期410、未知长度411、传输超大413、错误媒体415；失效已消费事实不因TTL重新执行。GET文件元数据/下载使用ready ID，临时upload_id不可读。

下载统一octet-stream/attachment、编码后的filename*、nosniff/CSP，按64KiB块复核认证与正文权限，内容不可用410、存储暂不可用503。源运行/代码/文件逻辑名不通过默认元数据投影泄露。无路径/镜像/挂载/凭据HTTP接口。上传台账兼容、所有权和传输取舍见 [ADR-0025](../decisions/0025-http-identity-and-uploads.md)；暂存回收和数据清理仍按T-15实施，不由HTTP后台自动执行。


## T-14 工作台实际入口

`DrovApp(..., workbench=True)` 默认提供 `/workbench/`，`/`、`/workbench` 跳转到该地址；`workbench=False` 关闭页面而保留HTTP。无认证只允许固定页面/静态资源 GET/HEAD，仍验证Host/Origin/本地连接；没有token bootstrap或任意文件目录。浏览器在登录表单提交本地token或宿主应用credential，只在内存保存。`GET /v1/agents -> AgentCatalog(items=[AgentSummary(name, revision)])` 经认证及应用级read后返回当前注册名/版本，不暴露配置，也不替代start授权。字段/schema/OpenAPI已由实际类型生成。

新增`GET /v1/session?session_id=...`与原路径接口同语义、同授权/分页，工作台采用查询形式无损传递包含斜杠/点段/保留字符的合法会话ID。页面复用前述会话/Run分页、inspection/context、responses/verify/cancel/recover、原上传消费和附件接口，没有新运行状态或存储字段。默认元数据，显式正文；清单中的provider内容仍不可见。重连不提交任务；未知start/response回执显式复用原请求重试。正文被拒绝时清除正文/文件名/Blob视图，后到旧请求不恢复内容。使用与数值限额见 [工作台说明](../../workbench/README.md) / [工程方案](engineering.md)，取舍和兼容边界见 [ADR-0026](../decisions/0026-local-workbench.md)。

## 离线 Session 逻辑清理（T-15 首批）

`from drov.storage.maintenance import SQLiteMaintenance, SessionDeletionPlan`。这是可信宿主管理员的文件能力，不接收普通服务端请求；显式提供现有库，带文件的库必须同时提供原 LocalArtifacts，并先停止 Runtime。构造无 I/O，进入异步上下文后取得原 SQLite/字节锁；冲突返回 StorageInUse，不强抢或关闭原持有者。

```python
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope


async def clear_existing_session(database, scope: SessionScope, *, files=None):
    async with SQLiteMaintenance(database, artifacts=files) as maintenance:
        plan = await maintenance.preview_session(scope, include_memory=False)
        if plan.blockers:
            return plan
        return await maintenance.delete_session(plan)
```

SessionDeletionPlan 包含 scope/include_memory、全库及阶段清单的 HMAC fingerprint、各类记录数量、blockers、保留/清理的 Artifact 数量、无引用阶段数量和 bytes_reclaimed=0；没有用户输入、文件名、参数或正文。删除在同一事务重算并完整比较 plan，过期/篡改返回 Conflict。默认保留 Memory；显式 include_memory=True 删除该用户/Agent 的整个 Memory scope，不仅是当前会话贡献，其他相关未结束/未知 Run 仍阻止此动作。

delete_session/delete-session 这一批只删除数据库正文/引用并保留最小事实；阶段字节、stage.json 和容量仍保留，不是完整个人数据擦除或容量回收。已清理 Session ID 不复用，原 request_key、换键、无键均 ContentGone；需要新会话 ID。Run/Artifact 在原 owner 当前 read 通过后返回 ContentGone/HTTP410，其他身份猜测及撤权仍404。保留共享引用与来源证明不授予额外权限；跨tenant/user/Agent的普通文本提及不参与另一作用域的清理判定。后续完整维护边界见 [维护策略](release-policy.md)，实际任务状态见 [progress](progress.md)。

无需账号/Key的实际合成例见 [maintenance_offline.py](../../examples/maintenance_offline.py)；CLI与当前限制见 [工程方案](engineering.md#离线维护入口t-15-首批)。

## 显式字节回收（T-15 第二批）

同一 `SQLiteMaintenance` 提供 `preview_reclamation(MemoryScope, expired_uploads=False) -> ReclamationPlan`、`reclaim(plan) -> ReclamationResult`、`resume_reclamation() -> ReclamationResult`。构造和权限边界沿前节，无 HTTP 管理路由。逻辑删除仍不自动删字节；回收精确指定 tenant/user/Agent，任一引用保护整个 stage。

ReclamationPlan 包含 scope、expired_uploads、as_of、fingerprint、stages、bytes_eligible、capacity_eligible、uploads 和按原因的 protected 数量。预览不修改原库/文件，不返回正文、文件名、路径或阶段ID。reclaim 使用原完整计划，数据库/引用/清单/候选文件身份变化或计划篡改返回 Conflict。只有显式 expired_uploads=True 才选择 as_of 时已过期且未消费的上传；已消费输入不按 TTL 删除。

ReclamationResult 包含 completed_stages、bytes_reclaimed、capacity_released；字节数是本次确认完成阶段的原库存数据长度，容量另含原预留及清单额度。中断前已删字节可计入恢复完成的阶段，不能将多次结果当成独立磁盘/费用计量；每阶段持久 done 才是累计依据。重复完成后 resume 返回零。

pending 意图使普通 SQLiteStorage/Runtime 打开返回 PersistenceUnavailable；离线入口可用 resume_reclamation 完成同一次删除，其他维护动作先返回 Conflict。引用/租约或文件身份与原台账不符时保留 pending 并拒绝，不强制跳过。此阻断用于本次删除，不能代替旧备份恢复的 recovery barrier。有效清单以外的旧孤儿/未知项继续拒绝，不能据猜测删除。POSIX、安全擦除/备份限制和具体恢复命令见 [工程方案](engineering.md#显式字节回收与中断恢复) / [ADR-0028](../decisions/0028-resumable-byte-reclamation.md)。

## 显式放弃遗留运行（T-15 第三批）

同一SQLiteMaintenance提供 `preview_abandonment(RunContext, run_id, reason=...) -> AbandonmentPlan`、`abandon_run(plan) -> AbandonmentFact` 和 `get_abandonment(RunContext, run_id) -> AbandonmentFact`。仍是持原排他锁的宿主文件管理能力，无HTTP管理路由；不可强抢原拥有者。仅非终态可首次放弃，需显式1–512字符非空白可打印单行原因；它是长期保留的管理说明，不从用户正文自动生成。

预览包含scope/Run、原status/state_version、unknown_operations/retained_leases数量和全库/阶段fingerprint；完整计划变化返回Conflict。放弃同事务结束为failed/data_abandoned、retryable=False/recoverable=False，保留已知用量并标complete=False；prepared记not_executed，dispatched/unknown及已知结果原样保留，当前pending交互关闭。原批准/恢复不能再派发，同键仍返回原失败Run；取消和放弃含义不同。

AbandonmentFact是不可变管理回执，包含opaque Run/Session、Agent、终态/不可恢复标记、最终版本/原状态、原因、未知operation/tool/revision/effect/status、放弃时保留租约数量和时间；无输入/参数/代码/文件名或明文owner/request_key。普通SDK/HTTP失败消息不含管理原因，原因仅在这个管理员查询中返回。提交不确定后须先重开原存储，同一完整计划重试返回同一回执，不再增事件/版本；换计划冲突。已清理正文后仍可查事实，同scope旧Session/请求墓碑仍Gone。

只有事实与当前failed/data_abandoned/版本/归属及未知操作逐项匹配，Session逻辑清理才允许去掉对应未知正文；无事实或不匹配仍拒绝。未确认销毁租约始终阻止清理，放弃不删租约、不调用后端、不退预算，也不自动回收字节。资源清理仍需原资源/后端的独立确认；保留租约数量只是放弃当时的快照。恢复旧备份的屏障仍是独立能力。可运行的无Key/预算SDK/stream例见[abandonment_offline.py](../../examples/abandonment_offline.py)，取舍见[ADR-0029](../decisions/0029-offline-run-abandonment.md)。


T-15一致备份管理员接口（ADR-0030）：`await maintenance.backup(new_directory)`，`await SQLiteMaintenance.inspect_backup(bundle)`，`await SQLiteMaintenance.restore(bundle, new_directory)`，`await maintenance.get_recovery_barrier()`。完整清单/新目标路径只属于可信本地管理员；备份为原始数据，不是普通用户导出。目标不可覆盖；返回RestoreResult中的数据库与可选原配对文件路径/限制。副本发布前持久屏障阻止所有接收与respond/resume/recover/verify及新内核派发，HTTP返回409/recovery_barrier；旧同键也拒绝。查看仍按原权限。无通用自动/force解锁入口，独立业务核实/事实补齐前保持阻断，具体文件/耐久性边界见[ADR-0030](../decisions/0030-quarantined-consistent-backups.md)。


### 显式兼容迁移与授权导出（T-15收尾）

`await maintenance.preview_migration()`返回具名v1-compatible-defaults计划；`await maintenance.migrate(plan, backup_directory=new_directory)`先备份，再原子回填早期v1兼容字段并保存MigrationReceipt。完整计划幂等，未知版本/profile拒绝，原状态/事件/去重和租约费用不变；无实际变化拒绝写表。不是任意未来schema转换器，见[ADR-0031](../decisions/0031-compatible-v1-record-migration.md)。

`await runtime.export_run(run_id, context=actor, include_content=False, artifact_ids=(), after=None, limit=100, max_bytes=16*1024**2, redactor=None)`返回RunExport。默认元数据；正文另需read/view_content，完整上下文在独立context_manifests，原事件页继续只有摘要。每个附件显式ID、属于该Run且逐项授权/hash校验，最多16个；文件含原ArtifactRef及独立副本size/hash/Base64。原分页/游标规则、最多500事件且受Runtime上限，容量默认16MiB/显式最高64MiB。ExportRedactor处理JSON副本，可脱敏正文或附件（重算副本hash），不改身份/权限/页游标或引入未选择来源；转换后重查权限。没有自动外传/保存或HTTP导出路由，详见[ADR-0032](../decisions/0032-authorized-portable-exports.md)/[合成例](../../examples/export_offline.py)。

恢复屏障也拒绝cancel和新的data_abandoned：旧prepared不能证明备份之后未派发。已有不可变放弃回执仍可查，纯观察/同义迁移/备份保留；无通用自动/force解除。

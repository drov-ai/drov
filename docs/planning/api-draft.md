# 公共 API 草案 v3

状态：P4 v3 基线；T-02 至 T-07 已实现定义/存储/生命周期、只读 Agent 闭环、授权事件/结果读取及上下文选择/清单；Memory、审批恢复和真实模型/服务入口仍未实现。T-06/07 细化见 [ADR-0010](../decisions/0010-read-only-agent-loop.md) / [ADR-0011](../decisions/0011-context-assembly.md)。更新：2026-10-13。

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

T-06 已实现表中的 start/run/wait/cancel/inspect/events/stream 与 Agent.run/run_sync。T-07 已实现 get_context；respond/resume/verify/recover/sessions/session 仍未实现，不能将表中设计入口当作已有功能。当前只接收含 callable 的 read 工具，Memory/write/unknown 接收前拒绝；所有 ASK 均失败为 interaction_unavailable。inspect.context_manifests 只返回本次事件页中 context.prepared 对应的摘要（sources=[]、content_visible=False），沿事件游标可遍历全部索引；get_context 返回完整来源清单，include_content=True 返回选入内容/预览，provider 正文始终不可见。旧完整轮次确定性选取，保护项无法装入时提前失败；清单预算/粗估包括协议和 schema，供应商已报告用量单独关联。查询按 context 的 tenant/user 隔离，跨归属查看和审批尚未交付。

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
# API 设计片段：authenticated_reviewer 来自应用认证/授权边界。
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

真实派发目前是私有内核接点，只有 read 可执行；write/unknown 明确 not_executed/effect_not_supported，ASK 保存 prepared/approval_required 而不制造 request_id。T-06 已接通 Agent.run/Runtime.start 只读路径，ASK 在 Run 收尾为 failed/interaction_unavailable；完整审批/核查仍待 T-09。结果必须经 codec 和大小校验；非法输出不当成功，None 用内部 has_result 区分可用结果。只读瞬态错误用 `drov.errors.TransientToolError`，其他错误/超时/非法结果不自动重试。排队/线程与原子记录规则见运行契约第 4 节和 ADR-0009。


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

## 内置 Memory 与上下文

`Memory(retrieval="required", writeback="explicit")` 是资源/策略描述，使用当前 Runtime 的事务存储；不是打开中的连接。可配置 best_effort 读取与期限，默认值及最近/键/子串检索方式见 [工程方案](engineering.md)。

它注册 memory.remember、memory.recall、memory.forget 三个逻辑工具名。模型供应商若限制工具名格式，适配器必须建立无冲突的导出名映射并记录映射；不能改变权限系统使用的逻辑身份。

```python
# API 设计片段：explicit 指通过明确工具写入，不指后台自动提取。
agent = Agent(
    name="personal-assistant",
    revision="1",
    model=model,
    memory=Memory(retrieval="required", writeback="explicit"),
)
policy = PermissionPolicy(allow={"memory.remember", "memory.forget"})
```

Memory 工具仍受用户作用域与应用权限限制；allow 只在该配置启用时生效。工具成功意味着本地写入和操作结果共同提交；任务 completed 不保证模型调用了保存工具。通过 inspection 的 memory.changed 事件及测试数据验证记忆行为。

当前 ContextPolicy 字段为 memory_fraction/memory_max_tokens；输入预算与工具预览大小使用 Limits.max_input_tokens/output_reserve_tokens/tool_preview_bytes。T-07 在 Memory 未启用时不占用 Memory 子预算；T-08 再接入实际检索。每次新的模型输入都有 ContextManifest；其选入顺序、required/best_effort 时序、删除与历史保留边界唯一规定在行为契约第 7 节。

## 应用服务与资源

```python
# API 设计片段：映射键与 Agent.name 一致。
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

T-02 已将下列 DTO/配置与模型协议落成 Python 类型，并生成 [schema 文档集合](../reference/schema-v1.json)；T-13 才生成 OpenAPI，目前没有可调用服务。实际模块导出见 [定义参考](../reference/README.md)。数据值不允许任意 Python 对象；类型子集与严格校验见 [工程方案](engineering.md) / [ADR-0006](../decisions/0006-python-contracts.md)。运行入口按上文实施状态使用，含 Memory/审批/服务的片段仍为设计。

### 公共配置与标识

- 文本 input 为非空 str；ID 为非空不含控制字符的字符串。用户提供的 tenant_id/user_id/session_id/name/revision/request_key 最多 128 字符，生成的 run/request/operation/model_call ID 是不透明 UUID 字符串，不编码用户名或密钥。
- RunContext 的公共归属字段为 tenant_id、user_id；权限/依赖来自可信宿主，不能从模型或 HTTP body 覆写。Agent.run 未传 context 时只用本次临时身份，无法访问持久化 scope。
- Agent：name/revision 在持久化注册时必填，临时定义可都省略；两者须成对提供。T-02 model 接收 ModelAdapter 实例，集成/资源描述由后续任务装配；tools 接受 ToolDefinition（仅 schema）或 @tool 产生的 PythonTool；进入执行配置时必须具备真实 callable。instructions 默认空串、tools 默认空集合、output_type 默认 str，memory 默认 None，context_policy/limits 使用工程默认值。相同对象可在各入口复用，但同一有状态客户端的所有权不能隐式转移。
- Runtime：agents、storage、policy、authorizer、dependencies、limits；dependencies 默认空映射、policy 默认保守规则、默认 authorizer 只允许本人记录/控制。跨用户 reviewer 必须由应用 authorizer 明确允许。资源只在进入 Runtime 时打开。
- ModelCapabilities：text、tools、structured_output、streaming 为 bool，context_window 为正整数或 None；未知能力不得默认为支持。OpenAIResponses 显式传 model 与 capabilities，密钥仅环境/借用客户端配置，不能作为可持久化 Agent 字段。
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
| memory.remember | key/content: str，expected_version: int；新建使用 0，更新先读取版本。返回 key/version |
| memory.recall | key/query: str 或 None（不能同时非空），limit: int（1–50，默认 5）；返回带 key/content/version/source/deleted 的列表，始终限定 scope；精确 key 查询可返回 content=None 的删除墓碑，其他模式排除墓碑 |
| memory.forget | key: str，expected_version: int；返回 key/version/deleted；重复 operation 返回既有结果，不重新删写 |

HumanInput 是可选工具描述，不自动注入每个 Agent。它是 Runtime 内置交互处理器，无外部写入，以 read 注册并遵守完整工具权限顺序；默认直接产生 input 请求，应用若另设 deny/ask 则仍生效。模型不提供用户回答。首期不支持任意工具返回一个未知控制对象就中断 Python 栈。S-03-E 的 operator 变体增加 HumanInput(name="ask_item", input_type=ItemChoice)，收到合法 item_id 后模型继续提出写入工具调用，原审批规则仍生效。

Memory 模型导出名映射、JSON schema 可空/默认转换遵守工程方案。输出 dict 仅是工具返回值，不等于可以用任意 dict 作为严格结构化最终输出类型。

### HTTP 请求与错误

POST /v1/runs 的 JSON 字段为 agent_name、revision、session_id、input，以及可选 request_key/limits；服务从已注册定义选择 Agent，拒绝上传工具代码/模型凭据/Agent 定义。缺失 session_id 不自动创建匿名持久会话。响应与事件读取分离。

POST responses 的字段为 request_id、response（上述判别 union）；POST verify 只收 request_id 并执行已注册的只读 verifier；cancel/recover 不接受新执行身份或工具参数。会话由首次 start 原子创建；GET /v1/sessions 与 GET /v1/sessions/{session_id} 提供分页查看；GET /v1/runs/{id}/contexts/{model_call_id} 提供授权的清单查看。维护清理/迁移不开放普通 HTTP 路由。

HTTP 错误形状为 `{error: {code, message, request_id}}`：未认证 401；无权读取统一 404；已知资源的控制权不足 403；静态格式 400/422；会话/版本/响应冲突 409；已清理内容或失效历史游标 410；容量不足 429；存储暂不可用 503。执行失败仍通过已存在 Run 的 outcome 返回。SSE 头已发出后的错误用非 durable 控制错误结束连接，不能再改 HTTP 状态或伪造持久事件。

内容清理后的 request_key、数据放弃和旧备份 barrier 以 [维护策略](release-policy.md) / 行为契约为准；恢复旧数据不是允许旧操作再次执行。

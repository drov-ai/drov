# 三类参考应用与跨入口案例

状态：P4 案例设计；S-01-A/C 和 S-02 的 SDK/stream 离线路径已运行，审批案例及真实模型质量仍待后续验收。更新：2026-10-13。

这些案例用来检验通用框架，不限制 Drov 的业务范围。所有 `drov` 符号均来自 [API 草案](api-draft.md) 的拟议接口；本文件不提供可安装教程。模型参数在离线验证中使用 ScriptedModel，真实验证使用显式配置的 OpenAIResponses；知识检索是合成夹具或应用自己的工具，Memory 为内置事务存储。详见 [工程方案](engineering.md) / [质量计划](quality-plan.md)。所有场景遵守 [行为契约](runtime-contract.md)，不能单独放宽规则。

## S-01：资料检索与结构化报告

### 目的与能力

验证工具选择、类型化结果、上下文来源与流式服务。关联 G-01/02/03/04 和 C-01/02/04/05/07/10/13/15。

### 固定输入与数据建议

- 用户问题：“根据提供的资料，比较缓存方案 A 和 B，并列出各自的适用条件。”
- 测试资料包含两个明确来源、一个不相关片段、一个可能被截断为预览的长结果。
- 另准备资料不足和检索失败的变体。
- 预期由固定资料决定，不依赖实时网络搜索结果。

### 使用片段

```python
# T-06 只读 API 可用片段；model/knowledge 由应用提供。
from dataclasses import dataclass
from drov import Agent, tool


@dataclass
class Source:
    id: str
    text: str


@dataclass
class Report:
    conclusion: str
    source_ids: list[str]


async def research_example(model, knowledge):
    @tool(name="search_docs", effect="read")
    async def search_docs(query: str) -> list[Source]:
        return await knowledge.search(query)

    agent = Agent(
        name="researcher",
        revision="1",
        model=model,
        instructions="根据资料比较方案；引用来源 ID；资料不足时说明。",
        tools=[search_docs],
        output_type=Report,
    )
    result = await agent.run("比较缓存方案 A 和 B")
    if result.status == "completed":
        return result.output
    return result
```

这里的 dataclass 属于 P3 确定的 schema 子集，通过 Pydantic 2 做本地严格校验和供应商 schema 检查。参数 `knowledge` 代表测试检索服务或应用自己的适配器，不是 Drov 已提供的知识平台。

### 验收案例

| ID | 情况 | 预期结果 |
| --- | --- | --- |
| S-01-A | 两份相关资料充足 | 输出符合 Report；引用 ID 来自实际返回资料；结论满足人工预先定义的事实断言 |
| S-01-B | 检索工具超时或失败 | 运行保留工具错误；按照配置报告失败或给出明确受限结果，不伪造已检索成功 |
| S-01-C | 工具结果超出预算 | 能查看被选入、裁剪或截断的内容来源；引用关系不因处理而静默损坏；模型摘要不属首期 |
| S-01-D | 资料不足 | 明确说明证据不足；不能靠输出 schema 正确就判定任务成功 |

T-06 已交付 [离线可运行例子](../../examples/README.md) / [research_offline.py](../../examples/research_offline.py)：真实合成语料检索、ScriptedModel 工具结果检查、Report 严格输出及实际引用集合核对，SDK/stream 共用内核。该例回答为预设夹具，验收 S-01-A 离线契约。T-07 的 [context_preview_offline.py](../../examples/context_preview_offline.py) 验收 S-01-C：实际长工具结果、带引用/版本的预览、get_context 清单核对及 full_document_read=False；SDK/stream 共用内核。完整历史组选取/排除与预算失败另由 [test_context.py](../../tests/test_context.py) 验证，真实模型质量仍待 T-11/T-16。

## S-02：跨会话个人助手

### 目的与能力

验证长期记忆、作用域、检索时序、持久化与上下文检查。关联 G-01/04/05 和 C-05/06/07/10/11/12/15。

### 固定输入与数据建议

- Alice 在第一次会话声明“偏好简短中文回复”。
- 同一用户的新会话询问回复偏好；Bob 使用相同应用但没有该记忆。
- 构造检索延迟、存储失败、记忆更新和删除变体。
- 这些偏好均为测试数据，不使用真实个人材料。

### 使用片段

```python
# 拟议 API，未实现。两个阶段应在测试中分别启动新的进程。
from drov import Agent, Memory, PermissionPolicy, Runtime, RunContext, SQLiteStorage


def build_personal_agent(model):
    return Agent(
        name="personal-assistant",
        revision="1",
        model=model,
        instructions="使用当前用户的记忆；只有保存工具成功才告知已保存。",
        memory=Memory(retrieval="required", writeback="explicit"),
    )


async def remember_phase(model, db_path):
    agent = build_personal_agent(model)
    caller = RunContext(tenant_id="local", user_id="alice")
    policy = PermissionPolicy(allow={"memory.remember", "memory.forget"})

    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(db_path),
        policy=policy,
    ) as runtime:
        result = await runtime.run(
            agent,
            "请记住我偏好简短中文回复。",
            session_id="alice-01",
            context=caller,
            request_key="remember-001",
        )
        if result.status != "completed":
            return result
        cursor = None
        remembered = False
        while True:
            inspection = await runtime.inspect(
                result.run_id,
                context=caller,
                after=cursor,
            )
            remembered = remembered or any(
                e.type == "memory.changed" and e.data["action"] == "remember"
                for e in inspection.events
            )
            if remembered or not inspection.has_more:
                break
            cursor = inspection.next_cursor
        assert remembered
        return result


async def recall_phase(fresh_model, db_path, user_id="alice"):
    # 重建 Agent 描述和 Runtime；依赖同一数据库，而不是旧 Python 内存。
    agent = build_personal_agent(fresh_model)
    caller = RunContext(tenant_id="local", user_id=user_id)
    policy = PermissionPolicy(allow={"memory.remember", "memory.forget"})

    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(db_path),
        policy=policy,
    ) as runtime:
        return await runtime.run(
            agent,
            "我偏好什么回复方式？",
            session_id="preferences-02",
            context=caller,
        )
```

内置记忆工具成功时，Memory 修改、操作结果和 memory.changed 事件已经共同提交。无需 sleep，也不依赖关闭 Runtime 才触发后台写回。completed 本身不能证明保存工具被调用，示例额外检查写入事件；正式测试还必须核验保存内容及作用域。

T-08 已交付 [memory_offline.py](../../examples/memory_offline.py)：普通/stream 各七个独立进程验证保存、新会话读取、三方向隔离与删除后读取；[test_memory.py](../../tests/test_memory.py) 核对工具事务、版本、检索期限、来源及固定重试快照。脚本回答只作离线断言，真实模型质量仍待 T-11/T-16。

测试驱动应等待第一个进程关闭，再在新进程重建所有资源调用 recall_phase。只在同进程依次调用这两个函数，仅说明 API 用法，不算跨进程验证。读取被拒绝或写入失败都必须显式报告。

### 验收案例

| ID | 情况 | 预期结果 |
| --- | --- | --- |
| S-02-A | 记忆提交后关闭进程，再创建新会话 | 同一用户/Agent 可读取已提交记忆；新建 Agent、Runtime 和模型客户端，无旧内存依赖；核验内容与版本 |
| S-02-B | 记忆检索延迟 | required 超时在模型调用前失败；best_effort 超时不向当前调用补入迟到内容，manifest 标明 skipped |
| S-02-C | Bob 查询 Alice 的偏好 | 存储查询按租户/用户/Agent 隔离；同名 session 不能混用记忆；模型不能覆盖可信归属 |
| S-02-D | 写入失败或删除记忆 | 失败不返回成功工具结果；forget 后不再参与新检索；不把既有对话/Trace 伪称已被删除 |

## S-03：带审批的操作助手

### 目的与能力

验证工具权限、审批、补充输入、恢复与外部操作边界。关联 G-01/03/05 和 C-02/08/09/10/11/12/13/15。

### 固定输入与数据建议

- 一个模拟事项服务，支持读取、更新和按请求标识查询操作结果。
- 模型提出把事项状态更新为“处理中”；写入必须审批。
- 使用可计数的模拟写入服务，构造响应丢失、重复审批、权限撤销和进程中断。
- 补充输入变体：用户未给出事项标识，先提出澄清请求，不猜测要修改的对象。

### 使用片段

```python
# 拟议 API；items 是借用的测试服务，context 来自应用可信边界。
from drov import Agent, Runtime, SQLiteStorage, ToolContext, tool, ApprovalDecision


def build_operator(model):
    @tool(name="update_item", effect="write", approval="required")
    async def update_item(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        return await ctx.deps["items"].update(
            item_id=item_id,
            status=status,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            request_id=ctx.operation_id,
        )

    return Agent(name="operator", revision="1", model=model, tools=[update_item])


async def propose_phase(model, items, db_path, caller):
    agent = build_operator(model)
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(db_path),
        dependencies={"items": items},
    ) as runtime:
        result = await runtime.run(
            agent,
            "将事项 item-01 更新为处理中。",
            session_id="operations-01",
            context=caller,
            request_key="update-001",
        )
        if result.status == "suspended":
            approvals = [r for r in result.pending if r.kind == "approval"]
            if approvals:
                return result.run_id, approvals[0]
        return result


async def approve_phase(fresh_model, items, db_path, run_id, request_id, reviewer):
    # 在实际人工批准之后，新进程重新注册原 revision 并解析依赖。
    agent = build_operator(fresh_model)
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(db_path),
        dependencies={"items": items},
    ) as runtime:
        return await runtime.resume(
            run_id,
            request_id=request_id,
            response=ApprovalDecision(approved=True),
            context=reviewer,
        )
```

propose_phase 退出时运行已经 suspended，所以 Runtime 只清理本进程资源，不取消已保存的审批。approve_phase 可以在之后的新进程运行；此片段使用默认 authorizer，因此 reviewer 是有读正文权限的原用户。跨用户审批须向 Runtime 注入应用 authorizer，并分别授予响应、read/view_content；只有响应权限时用 respond 取得回执。继续执行的身份始终是原运行用户。

代码给业务接口传入 operation_id，便于结果查询或后端去重；这不等于接口已具备幂等保证。测试中必须明确模拟服务的契约。未知写入需要核查，resume 不能自动用批准人的高权限重试。

S-03-E 的补充输入变体在 tools 中另注册 `HumanInput(name="ask_item", input_type=ItemChoice)`，ItemChoice 是仅含 item_id 的对象 schema；模型用 prompt 提出问题，用户通过 InputResponse(value={"item_id": "item-01"}) 回答。这个值成为原 HumanInput 调用的结果，再由模型提出 update_item 调用并等待审批；不会从 update_item 函数中断处继续或重新执行一次旧写入。

### 验收案例

| ID | 情况 | 预期结果 |
| --- | --- | --- |
| S-03-A | 请求写入，随后批准或拒绝 | 审批前写入次数为零；批准针对已展示的具体调用；拒绝不执行该写入 |
| S-03-B | 等待审批时终止进程，再恢复 | 待处理请求和归属可恢复；不能因进程重启自动批准 |
| S-03-C | 重复审批、参数变更、错误用户或权限撤销 | 同响应返回已有回执而不重复派发；冲突响应拒绝；参数改变必须新请求；权限撤销阻止执行 |
| S-03-D | 外部更新成功但响应丢失 | 暂停核查并保留原 operation_id；不能盲重试；有取消标志时核查后也不得重新派发 |
| S-03-E | 缺少必要输入 | 暂停并请求补充信息；收到合法响应后继续，不混用审批响应类型 |

## 跨案例验证

| ID | 验证目标 | 方式与边界 |
| --- | --- | --- |
| X-01 | SDK、流式、HTTP 使用同一运行规则 | 用固定模型替身驱动同一案例，比较结果状态、工具策略和事件关联；真实模型下不要求文本逐字相同 |
| X-02 | 资源生命周期一致 | 成功关闭时释放自有资源；借用资源不关闭；启动失败逆序清理；活动调用无法结束时明确 ShutdownIncomplete。T-04 验证注册/资源及私有任务与实际线程引用，见 [记录](../validation/t04-runtime.md)；跨 MCP/HTTP 入口仍待 T-12/T-13 |

## 从说明片段到可运行示例

P3 已指定依赖、完整夹具、模型适配器及规则断言，并把 [B-01 至 B-15](runtime-contract.md) 中首期适用项映射到质量计划和任务；B-14 属后续。已实现的只读及 Memory examples/测试见上文；审批设计片段尚未导入或运行。

三类 Agent 都需通过同一 DrovApp 入口复测。X-01 使用显式 Runtime 的相同权限、存储和限额配置对照 HTTP/流式；不将临时 Agent.run 与持久化 Runtime 的配置差异判为内核差异。

T-05 已验证 S-01-B 的只读瞬态重试/超时及 S-03-A/C 的权限否决、ASK、可信参数/归属和重复操作不派发部分，见 [工具验证](../validation/t05-tools.md)。完整 Agent 案例、人工审批响应和未知写入仍由后续任务完成，不能将计数夹具写成三个场景已跑通。

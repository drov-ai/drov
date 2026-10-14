# 首版工程方案

状态：工程选择与默认值由本页维护；当前任务状态统一见 [progress](progress.md)，T-10 完整证据见 [专项验证](../validation/t10-faults-cancellation.md)。文件/代码沙箱、E2B、真实适配器和服务仍未实现。更新：2026-10-14。

本文件维护技术栈、目录、支持矩阵和默认配置。能力范围以 [能力表](capability-matrix.md) 为准，公共字段在 [API 草案](api-draft.md)，执行语义在 [行为契约](runtime-contract.md)。决定及替代方案见 [ADR-0004](../decisions/0004-first-release-engineering.md)。

## 1. 最小技术栈与依赖边界

| 部分 | 工作选择 | 选择原因与边界 |
| --- | --- | --- |
| Python | CPython 3.11 起；首版支持矩阵为 3.11–3.14 | 使用标准 asyncio；3.11 已有 timeout 等原语。3.15 先作为非阻塞兼容探测，不宣称支持；不支持 PyPy/free-threaded 模式 |
| 核心类型 | Pydantic 2，内部数据类/Protocol 按需使用 | 统一校验、JSON schema 和序列化；不要求每个应用工具都继承框架基类 |
| 存储 | 标准库 SQLite，经 aiosqlite 访问；portalocker 排他锁 | 一个本地文件、一个 Runtime、一个连接；不引入 Redis、ORM 或消息队列 |
| 模型 | 核心 ModelAdapter 协议 + ScriptedModel；首个线上适配器 OpenAI Responses | Drov 自己实现运行循环，复用官方 Python 客户端；不依赖其他 Agent 框架的循环 |
| MCP | 官方 Python SDK v2，先支持 stdio 客户端 | 当前官方文档标注 v2 稳定线、Python 3.11+；首版不同时做远程 OAuth/多传输 |
| 服务 | FastAPI + Uvicorn；标准 HTTP + SSE | 通过 lifespan 装配 Runtime；一个 worker，SDK-only 不导入 Web 栈 |
| 工作台 | TypeScript + Vite 构建静态页面，不用 UI 组件框架；构建使用 Node 24 | 页面只调用 Drov HTTP；构建产物装入 Python wheel，终端用户无需 Node；依据 E-23 |
| 项目工具 | uv、Hatchling；Ruff、mypy、pytest、pytest-asyncio | 开发锁文件、标准 wheel/sdist、异步回归；需要的 HTTP/浏览器测试依赖放开发组 |
| 官网文档 | MkDocs 静态构建 + 自定义首页/主题样式，Cloudflare Pages | 官网和文档共用导航、版本与品牌素材；不把 Python Runtime 部署在静态站点中 |

依据：[E-09 至 E-23](evidence.md)。T-01 已解析并在仓库生成精确 `uv.lock`，安装检查不等于运行时集成兼容；已提交与 CI 状态见 progress。T-14 再生成前端锁文件。库的依赖下界从实际安装的版本开始，范围内其他版本仍需后续兼容检查；锁文件用于开发/CI，用户安装库不会自动继承开发锁。后续适配任务补充最低版本与锁定版本的协议/行为测试，不能只测试维护者电脑。

## 2. 一个 Python 分发包，按需安装

Python 导入名使用 `drov`；PyPI 分发名暂定 `drov`，占用和控制权待发布准备核实。若不可用，变更分发名不自动变更导入名。npm 组织保留，v0.1 不为凑产品线发布 JS SDK。

T-01 已声明 extras：`openai`、`mcp`、`server`，目前只安装依赖，没有对应 Drov 适配器。核心依赖为 Pydantic、aiosqlite、portalocker、typing-extensions；目标是 SQLite 开箱可用，无需额外数据库服务，T-03 已实现事务存储，T-04 已实现 Runtime 生命周期，T-06/07 已实现只读 Agent/上下文；T-09 已实现显式恢复调度。extras 只选择依赖，不生成不同内容的 wheel；T-14 后统一 wheel 携带预构建工作台静态文件，`server` 增加运行 HTTP 服务所需依赖，SDK-only 导入不加载它们。开发依赖放 dev group，文档/评测组在对应任务引入，不成为 SDK 必装依赖。首版不拆多个协同发版的 Python 包。E2B experimental extra e2b 只在 T-10C 真实适配时新增，当前并未声明或安装。

以下是完整首版的目标布局。T-01 只建立元数据包、测试/构建检查和 CI，其他模块按任务创建，不以空壳占位：

```text
pyproject.toml / uv.lock
src/drov/
  __init__.py / py.typed        公共入口和类型声明
  agent.py / types.py / errors.py
  runtime/                    状态推进、交互、取消和恢复
  tools/                      schema、权限、依赖注入
  context/ / memory/          输入清单和事务记忆
  storage/                    协议、内存/SQLite、版本迁移
  models/                     协议、脚本替身、OpenAI 适配器
  sandbox/                    T-10A：Python 工具/后端协议/Docker 执行
  artifacts/                  T-10A：私有字节、暂存与引用存储
  integrations/mcp/           可选 MCP 客户端
  integrations/e2b/           T-10C：可选 adapter，当前不创建
  server/                     可选 HTTP、认证边界、静态资源
  cli.py                      轻量启动/诊断/离线维护入口
workbench/                    前端源码和独立锁文件
examples/                    三类可运行案例
tests/                       单元、契约、集成、进程故障
evals/                       固定任务、评分规则、报告模板
docs/                        用户文档、规划和决策
.github/                     CI、贡献模板和发布工作流
```

核心模块不得反向导入 server、integrations 或具体供应商；`from drov import Agent` 不加载可选客户端。可选能力未安装时给出明确的 extra 提示，不在运行时自动安装。导入无网络、文件写入、全局事件循环或隐式客户端创建。

## 3. 类型与扩展接口

应用对话输入首版为非空文本；T-10A 增加独立 artifacts 宿主参数，作为受控文件工具数据，不是多模态模型消息入口。多模态和任意消息列表仍后续。工具参数支持 JSON 标量、有限枚举、list、嵌套 dataclass/Pydantic BaseModel、可空类型；拒绝 Any、位置专用参数、变长参数、未解析引用和任意 Python 对象。ToolContext 按类型注入，不能出现在模型 schema。

工具返回可以是可序列化的上述类型及字符串键 dict；返回 schema 与调用参数 schema 分开。`output_type` 首版支持 str 或根为对象的 dataclass/BaseModel；嵌套类型遵循上述子集，不承诺任意 Pydantic schema 都能发给供应商。解析后本地校验，拒绝多余字段与 NaN/Infinity；不静默把字符串转为数字。模型参数/工具 JSON/最终输出拒绝 datetime/bytes/文件句柄等非 JSON 类型，应用自行显式转换；ArtifactInput.data 和授权下载的 bytes 是独立有界文件通道，不进入这个 codec。T-02 明确拒绝递归、RootModel、alias、自定义 validator/serializer/构造钩子、自定义 schema 和嵌套 Annotated 约束；常见 Field 数值/长度/字符串正则约束会保留并验证。普通对象额外字段一律拒绝。详见 [ADR-0006](../decisions/0006-python-contracts.md)，复杂类型不得静默降级。

注册时生成并校验 schema。供应商严格模式导出时拒绝不支持的结构，不降级成宽松 JSON；可将有默认值的参数导出为必须显式提供的字段，并在说明中展示默认值，但不能把 null 偷换成默认值。Nullable 才接受 null。供应商拒绝、输出截断、结构不合法分别报告，不自动调用另一模型“修复”。

| 扩展边界 | 最小接口责任 | v0.1 支持承诺 |
| --- | --- | --- |
| ModelAdapter | capabilities；生成归一化流；完整响应/工具计划/用量；关闭所有权 | 支持应用自建适配器；需通过公共契约测试 |
| Storage | 原子事务、条件更新、作用域查询、版本读取、所有权 | 内存与 SQLite；协议暂为内部，不承诺外部数据库实现兼容 |
| Tool | schema、版本、effect、approval、执行；可选结果 verifier | Python 工具和 MCP 桥接；verifier 只读且有超时 |
| AuthAdapter / Authorizer | 解析可信身份；按 actor/action/resource 判断访问和控制权 | 应用可注入异步接口；HTTP 用户数据不能直接构造身份 |
| Memory | 首版策略描述 + 内置事务处理器 | 不开放任意外部写入后端冒用内置原子性 |
| 观测 | 只读事件订阅、元数据导出 | 本地查看/JSON 导出；OTel 后续，不放任回调改写状态 |

模型协议首版最小归一结果含 message、tool_calls、usage、provider_items；后者是需要往返的供应商协议数据，不是可展示的内部推理。流式增量必须以完整响应事件收尾才可提交工具计划。T-02 已实现 drov.models.protocol 与离线 ScriptedAdapter，生成 [schema](../reference/schema-v1.json)。ModelRequest 包含 messages/tools/output_schema/provider_items，ModelResponse 包含 message/tool_calls/usage/provider_items/finish_reason；generate 产出 delta/complete，aclose 为异步关闭。collect_response 拒绝缺少 complete、complete 后数据、重复 call_id 或不完整工具计划；不调用真实供应商。Authorizer 为 authorize(actor, action, resource)，AuthAdapter 为 authenticate(credential)，T-05 已实现 PermissionPolicy/OwnerAuthorizer 及 execute_tool/ToolResource，实际服务认证仍待 T-13。

T-05 @tool 返回 PythonTool，name 必填，revision 默认 "1"、effect 默认 unknown、approval 默认 policy；含参数/返回 codec 和真实 callable。同步/异步函数共用 schema 子集；生成器拒绝。只读瞬态错误用 TransientToolError，其他错误不默认重试。ToolPool 在 Runtime 打开时装配，线程只在真实提交时启动；有界等待先于派发标记，实际 Future 由内核持有。内部操作 payload schema=1，Checkpoint.data.tool_dispatch_count 保存预算预留；即使提交后撤权/取消使函数没执行也不回退，恢复不会把尝试变成免费次数。实际详情见 ADR-0009。

T-09 子范围复用 RunItem：interaction.data 包含 schema_version=1、request、request_hash、config_hash；receipt.data 包含 schema_version=1、response、actor、receipt，收据索引 request_id 且不可改写。Checkpoint.data.pending_plan 引用原 model_response，approval_grants 将 operation_id 映射到已消费的请求；原 ToolOperation schema=1 不增加字段。HumanInput 从注册 input_type 生成结果 codec，与普通工具一致进入指纹。累计 model_step_count/model_attempt_count/tool_dispatch_count/active_seconds 跨人工暂停保存。没有新 DDL/依赖，旧缺少新字段的记录仍可读，通用恢复兼容待 T-09 剩余范围验收。到期/授权/继续规则见运行契约第 5–6 节，实际证据见 [T-09 验证](../validation/t09-interactions.md)。

T-09 完整路径增加 pending_model_call/pending_model_attempt_count/pending_output，原输入/call ID、累计预算和已提交响应在恢复时保留；旧 engine=1 游标可从已提交事件/response_ids 重建，未知 engine 拒绝。ToolOperation schema=1 的可缺省 effect_resolution/evidence 表达核实事实，重新派发清空旧证据；无该字段的旧记录照常读取。verification.started/completed 为 schema_version=1 新事件类型；每次查询以 `verification:<id>:started/completed` 的两个不可变 receipt 子记录审计（record_type=verification），不增加 DDL kind。未终态同步事件/检查点，终态后只保存审计回执。@tool 的 async verifier 与 verifier_revision 进入有 verifier 的注册指纹；无 verifier 不增加该键。无新依赖/锁更新；验证见 [本轮记录](../validation/t09-reconciliation-recovery.md)。

## 4. SQLite、锁与 Memory

SQLite 启动要求运行库版本至少 3.35；检测实际 `sqlite3.sqlite_version`，不能只检查 Python 版本。采用 `journal_mode=DELETE`、`synchronous=FULL`、`foreign_keys=ON`、`busy_timeout=5000`，读取验证设置结果。选择回滚日志是首期单连接下的简化；WAL、多连接及网络文件系统不在支持范围。[E-15](evidence.md) 仅解释 SQLite 机制，不能替代进程故障实验。T-03 的实际文件/进程/取消验证见 [存储记录](../validation/t03-storage.md)。

所有 SQL 经同一连接；Runtime 事务调度锁包住完整的 BEGIN IMMEDIATE 到 COMMIT/ROLLBACK，普通查询也不得插入他人的事务中。aiosqlite 的逐条请求队列不等于业务事务隔离。取消等待提交时必须等数据库工作完成、确认提交或回滚后才释放锁；数据库状态仍不明则停止派发并保留所有权，不让另一协程继续使用不明事务。T-03 使用共享 Transaction 操作及类型化 records 表；业务行含归属索引，Session 未结束运行和活动交互由唯一索引约束。元数据/内部全局容量查询不作为用户数据入口。完整记录与公开投影分开；具体取舍见 [ADR-0007](../decisions/0007-transactional-storage.md)。

数据库路径规范化后，用同路径 sidecar 锁文件 + 同进程所有权登记阻止重复 Runtime。锁为非阻塞排他方式，失败报 StorageInUse；不按 PID 文件是否存在判断。崩溃后 OS 释放锁，残留文件不阻止重启。软链接须规范化；硬链接别名拒绝支持。首版只支持本机普通文件，文档禁止网络盘、同步盘和活跃数据库替换。此锁协调 Drov 进程，不能阻止忽略锁的外部程序直接写库。

Memory 保存 key、content、version、时间和来源，写入使用 expected_version（从未存在的 key 新建为 0，更新/删除必须使用读取到的版本）。删除保留不含正文的版本墓碑，避免删除再建发生版本回退；精确 key 查询可返回 deleted=True、content=None 的墓碑元数据，重建使用其版本。最近/子串检索只返回存活记忆。操作去重和 Memory 修改与工具结果同事务。

自动上下文读取默认取当前用户/Agent 最近更新的 5 条存活记忆；排序为 updated_at 降序、key 升序。显式 recall 可按 key 精确查找，或按 NFKC + casefold 后的完整查询子串匹配 key/content，再按上述次序分页。先在 SQL 中限定归属。空查询使用最近记录；不宣称中文分词、向量检索或语义相关性。检索超过预算的记录在 manifest 标出 excluded。这是可用的基础记忆层，复杂知识检索继续使用应用工具。

## 5. 首个模型与 MCP 路径

OpenAI 适配器拟名 `OpenAIResponses`，从 `drov.models.openai` 导入。必须显式传 model ID；不硬编码“最新模型”或替用户选择付费档位。ModelCapabilities 明确 text/tools/structured_output/streaming/context_window；未知窗口必须由应用填写输入上限。模型与价格在真实评测时固定，记录官方能力来源和实际测试结果。

采用官方异步客户端，Drov 管理请求预算；关闭 SDK 内部自动重试，避免双层重试。使用 Responses 的 function calling 与 structured output；工具始终由 Drov 授权后在本地分发，不启用供应商托管工具。显式 `store=False`、禁用自动截断，不使用远程 conversation/previous_response_id 代替本地历史；保留需要往返的响应项。需要 encrypted reasoning 项的模型只能在往返测试通过后列入支持；不将该内容解密或展示为思维链。`store=False` 不等于供应商零保留承诺，后者取决于服务政策与账户。[E-11](evidence.md)

通过 `parallel_tool_calls=False` 与 Drov 顺序计划共同限制派发；即使供应商返回多个调用，也不得在完整计划提交前执行。部分流、未知输出项、refusal、incomplete、429/5xx/超时均有适配测试。兼容某个 HTTP URL 不等于已经兼容所有 OpenAI-compatible 服务；额外供应商逐个通过契约测试再列支持。

MCP 先连接应用显式配置的本地 stdio 服务，完成初始化、分页发现、调用、超时和退出；记录协商协议版本。客户端构造在 Runtime 生命周期内，工具名映射含 server namespace。初始 schema 固定到注册 revision；运行中服务工具改变需新注册，不能改变待审批操作。MCP annotations 只作提示，effect/权限由应用明确映射；默认 unknown/ask。禁止从模型文本启动任意命令；应用配置可执行文件与环境白名单，默认不把模型 API Key 传给子进程。

仅启用 MCP tools；resources/prompts、sampling、elicitation 和远程认证属于后续范围。若服务要求尚不支持的能力，明确拒绝而非假装成功。Drov HTTP 事件流与 MCP 的传输协议不是同一功能。[E-12/13](evidence.md)

## 6. 默认限额

下列数值是起始工程默认值，**不是实测性能结论**。应用可显式覆盖；服务端管理员上限优先于请求值，只允许请求收紧限额。恢复沿用已保存限额，当前管理员更严格时再收紧。

| 配置 | 默认 | 规则 |
| --- | --- | --- |
| max_model_steps / max_tool_calls | 20 / 40 | 每次逻辑模型请求算一步，重试另计 attempt；每个实际派发计一次工具调用，未知写入计入 |
| Run 活动时间 / 单次推进 | 300s / 120s | 人工暂停不计活动时间；恢复不重置累计计数；推进指 start/resume/recover 到下一次暂停/终态 |
| 模型 / 工具 / 结果核查超时 | 60s / 30s / 10s | 模型/工具还受剩余推进与 Run 预算限制；显式 verify 为单独限时控制操作，取消/预算耗尽后可用于收尾；不能保证杀死线程或远端请求 |
| 模型 / 只读工具额外重试 | 1 / 1 | 仅瞬态错误；1s 等待并受总预算限制，供应商 Retry-After 在预算内遵守；v0.1 写入额外重试固定 0，不允许通过配置开启 |
| 审批/补充输入 / 核查提醒期限 | 24h / 24h | 前者过期失败，后者只提示仍待核查；无默认 Run 总墙钟到期，应用可设绝对 expires_at |
| required / best_effort Memory 等待 | 2s / 100ms | 清理迟到任务，实际语义见行为契约 |
| 输出预留 / 输入预算 | 2048 / 最多 16384 估算单位 | 有模型窗口时取可用余量；窗口未知且未显式设 max_input_tokens 时配置失败 |
| Memory 上下文份额 | 输入预算的 20%，且最多 2048 | 保护内容优先；不足以装入一条则排除并记录原因 |
| 最大文本输入 / 参数 JSON | 64KiB / 64KiB | UTF-8 字节，调用前校验 |
| 单条工具结果 / 单条记忆 | 256KiB / 16KiB | 结果超限记录已执行但输出不可用，不因结果过大重做副作用；自定义工具负责避免在函数内构造无限对象 |
| 工具结果进入模型的预览 | 16KiB | 包含原始结果引用和截断标记；不把截断的半段 JSON 当合法原结果 |
| 活动槽位 / 未结束 Run 总数 | 8 / 1000 | 推进与 verify 占活动槽位，未停止调用继续占用；普通人工等待仅占后者；start 超限不新建，响应/恢复超限不消费请求 |
| 同步工具线程 / 等待队列 | 4 / 16 | 饱和时派发前拒绝/等待受预算约束；不无限创建线程 |
| 订阅缓冲 / 单增量 | 256 条 / 32KiB | 超限临时增量丢弃并提示 gap；持久化记录按 seq 补读 |
| API 分页 | 默认 100，最多 500 | 明确 next_cursor/has_more；事件为 run_id:seq，Session/Run 列表为绑定查询的 opaque 游标，均重新授权 |
| Runtime 退出宽限 | 10s | 不能关闭则 ShutdownIncomplete，遵守资源与锁所有权 |

计数在派发前预留并持久化，进程恢复不能把失败 attempt 当免费次数。max_model_steps 限逻辑请求总数，另有每步最多两次尝试；模型 token/费用未知时以 unknown 记录而非 0。崩溃前活动时间先预留该调用允许时长，正常返回用实耗结算；结果不明时保守保留预留，不依赖崩溃进程报告最终时长。此策略会少给预算，代价在报告中说明。

初始文本 token 估算采用序列化内容 UTF-8 字节数 + 每次请求 256、每协议项 32 的显式粗估规则，工具 schema 和输出 schema 也计入；这是启发式估算，不是供应商精确计费或任意 tokenizer 的硬上界。ContextManifest 同时标记 estimator 与供应商已报告值，预算错误不静默改写历史。供应商报告 context overflow 时明确失败，后续可通过单独 adapter 增加 tokenizer。

T-02 的 Limits 字段名和配置 schema 由 [Python 类型](../../src/drov/config.py) / [生成文档](../reference/schema-v1.json) 给出。max_input_tokens 默认 None：已知窗口取 min(16384, context_window - output_reserve_tokens)，未知窗口必须由应用显式给上限，Agent 定义已前置校验。None 不是取消默认安全预算。write_tool_retries 只接受整数 0；上表默认值是工程决定，不是性能承诺。

## 7. 服务和工作台

首版单 worker；多 worker/自动热重载对同一数据库的竞争必须产生明确 StorageInUse，不能绕开锁启动。SDK、HTTP、UI 的身份、权限、限额和状态走同一 Runtime。

默认只有显式本地模式：绑定 127.0.0.1，随机本次启动令牌，检查 Host/Origin，工作台同源；浏览器用认证 fetch 订阅 SSE，不把令牌放 URL、持久日志或跨站请求中。首版不接任意 Origin。非本地绑定要求应用提供 AuthAdapter、Authorizer 和允许来源，否则启动失败。传输 TLS、账号系统与凭据轮换由宿主接入，Drov 不宣称自带完整 IAM。

工作台必须能：启动三个样例、列会话和运行、查看状态/事件/上下文来源与裁剪原因、展示工具参数、提交审批/澄清/核查、取消、重连和查看错误。它是本地开发界面；无工作流画布、远程组织管理、后台账号体系。UI 首版标记实验性，仍必须通过上述验收，不可用标签代替测试。

## 8. 实施前与实施中分界

T-01 已验证锁定依赖的本地安装组合，T-02 已验证类型、工具 schema 与归一模型协议；各集成行为仍由对应任务验证。T-01/T-02 负责把这些决定变成可导入的类型、锁文件和有意义的契约测试；发现版本不兼容时记录差异，不能偷偷切换架构。最低 Python、范围或语义变化需要同步 ADR 和验收。缺少真实 Key、发布账号或最终域名不阻塞这些任务。

## T-06 落地边界

RunController 连接接收、模型循环、只读工具、限额、历史和正文投影；RunEvents/RunStream 以有界临时缓冲和持久事件分页读取实现 SDK 订阅。模型瞬态重试只使用 TransientModelError，固定已保存输入、每次独立 attempt_id；ScriptedModel/ScriptedStep 为无网络离线适配器，额外调用失败。T-06 未增加依赖或改变 uv.lock。

Checkpoint.engine_schema=1；model_input/model_response payload 各 schema_version=1；session_sequence 在接收事务使用 Session 版本保存，避免相同时间戳的随机 ID 打乱历史。计数及活动预留保留原 Run 事实；正常返回按实耗结算，未停止调用保留预留与资源，禁止迟到结果变成成功。T-07 已替换完整历史模式为确定性组选取/预览/清单，仍对保护输入溢出前置失败。SDK 活动订阅至多 50ms 轮询提交，durable 事件不进入无界队列。显式 Runtime 的 ASK 支持审批/输入原子响应，临时入口仍失败；外部 write/unknown 单次派发且未知结果暂停核查；T-08 Memory 是专用事务处理器；完整细节见 [ADR-0010](../decisions/0010-read-only-agent-loop.md)。

## T-07 落地边界

内部 context/assembly.py 是无存储/模型副作用的选择器；RunController 负责已提交数据投影及输入/清单同事务。estimator 固定 utf8-json-bytes+request256+item32-v1，全请求 JSON 含工具/输出 schema 及 provider_items。源级 estimated_tokens 仅表示各自内容的 UTF-8 JSON 字节贡献，不是总估算的可加分解；总数以清单 estimated_tokens 为准。输出预留扣完窗口时 budget=0，并保留 rejected 清单。

预览上限包含完整标记/引用、UTF-8 文本和 JSON 转义；当前结果在预算不足时按契约确定性缩小，历史组不改变原事实。ContextManifest 新增带默认值的 estimated_tokens/reported_usage，公共生成 schema 同步；model_input/model_response/context payload 均为 schema_version=1，无 SQLite DDL、锁或依赖变化。旧 T-06 输入未保存清单，不回填虚假来源。API/选择/权限规则见运行契约第 7 节和 [ADR-0011](../decisions/0011-context-assembly.md)。

## T-08 落地边界

MemoryTool 是私有 schema 描述，不带应用 callable；Agent 自定义工具不可使用三个保留名。配置 Memory 时，模型 schema 和注册指纹包含内置工具（无 Memory 的旧指纹不变），描述构造不打开资源。MemoryDispatcher 与 OperationJournal.commit_in 共用 Runtime Storage 的事务；成功、CAS 冲突或结果超限等本地尝试消耗一次 tool_dispatch_count，不存在外部派发预留或 unknown。操作 payload schema=1 增加带默认空列表的 memory_sources，旧操作仍可读取；无新公共 DTO/schema、SQLite DDL、依赖或锁变更。

Transaction.recall_memory 的最近顺序使用 updated_at DESC/key ASC；子串检索按此顺序每页最多 100 行扫描已经限定 tenant/user/Agent 且 live 的记录，匹配 NFKC+casefold 后的完整子串，达到 1–50 条即止。没有新查询游标 API或语义/向量检索承诺。key/content 的规范 JSON 总字节数受 max_memory_bytes 限制，工具整体结果受 max_tool_result_bytes 限制；非法输出在修改前拒绝。

每次自动检索有一个内核持有的真实 Task；读取完成时间超过截止点也丢弃。取消后清理宽限固定 100ms，未结束的异常后端保留所有权并抛 PersistenceUnavailable；没有后台追补。保存输入的事务内复核/重查，Memory 消息以带 drov.memory 类型的 user 数据注入；实际 JSON 消息及协议开销计入 min(输入预算×memory_fraction, memory_max_tokens) 与全局剩余量，然后才选历史。超大记忆整条排除，不自动摘要；精确 token/模型使用质量不由此证明。最近扫描/子串和详细清单规模仍受记录量影响，尚未验收大规模检索性能。完整决定见 [ADR-0012](../decisions/0012-transactional-memory.md)。

## 9. 首版 Docker 代码与文件沙箱（T-10A 部分实施）

第三批内部控制/预检已按 [ADR-0019](../decisions/0019-docker-control-preflight.md) 实施：本地 POSIX Unix socket、Linux Engine/cgroup v2/API ≥ 1.41、同架构 ARM64/AMD64 固定 CPython 3.11 镜像与资源能力查询；构造无 I/O，open 不创建容器。内部 binding v1 包含实际 Engine/CLI/image/profile/限额与控制版本，不含随机配置目录。尚未绑定 Runtime 审批/Run 或开放 PythonSandbox；实际创建后/有效控制检查由固定可信探针验证，证据见 [本轮验证](../validation/t10a-docker-controls.md)。这不是任意代码或完整平台支持验收。

- 首个后端选择宿主显式配置的本地 Docker CLI/Engine，执行端为 Linux 容器；Drov 用 asyncio.create_subprocess_exec 的固定 argv 管理可信 CLI，代码仅作为容器内固定 Python runner 的输入，不拼进宿主 shell。核心无新增 Python Docker SDK 依赖；sandbox/ 模块在 T-10A 真实实施时建立，普通 SDK 安装/导入不需要 daemon。
- 镜像由宿主预装并以 repository@sha256 manifest 或显式本地 sha256:image-config-id 固定（ADR-0017），禁用可变 tag、执行期自动 pull/构建/安装依赖；固定非 root 用户与 Python 入口，固定覆盖 entrypoint、禁用镜像 healthcheck，拒绝镜像自动 VOLUME；镜像环境经白名单核验，不继承宿主环境/凭据。必交标准库计算及一个固定数据分析镜像（numpy/pandas/matplotlib，用于 CSV/PNG 案例）；依赖由宿主预构建并验收，执行期间不安装。T-10A 首批已有本地 ARM64 分析镜像、wheel 锁/许可与可信探针，见 [验证](../validation/t10a-artifact-staging.md) 和 [实际 recipe](../../sandbox/README.md)；这不是 SDK 任意代码后端/完整平台验收，标准库镜像保持原样。具体版本与三种 digest 区分按真实记录，不把本地 config ID 伪装为 registry manifest。
- 隔离配置固定：network=none、无端口发布/host namespace、无 bind/volume/device/docker socket 挂载、只读根文件系统、有限私有 tmpfs、非 root、cap-drop=ALL、no-new-privileges、保留有效 seccomp；缺少所需能力或实际配置不一致即拒绝，不自动降级。[Docker 运行配置](https://docs.docker.com/engine/containers/run/) / [CLI 参数](https://docs.docker.com/reference/cli/docker/container/run/) / [seccomp 文档](https://docs.docker.com/engine/security/seccomp/) 支持这些机制的存在，不证明 Drov 已实现或抵抗所有内核攻击。
- 限额必须由 daemon/内核实际执行，不能仅是 Drov 字段；内存与 swap 总额一并限定，不启用 oom-kill-disable；工作区/tmp/shm 有限，资源耗用包含 tmpfs。使用有界 stdout/stderr 管道，先按原始字节限制，显示以 UTF-8 errors=replace 增量解码，区分边界截断与非法字节替换；禁用容器持久日志（log driver none），避免日志绕开输出上限占满磁盘。序列化结果仍受 max_tool_result_bytes，超限按结果不可用收尾，不重跑代码。丢失未保存输出时按契约报告不可用，不依靠无界日志回填。依据：[资源限制](https://docs.docker.com/engine/containers/resource_constraints/) / [tmpfs](https://docs.docker.com/engine/storage/tmpfs/)。
- 模型代码不做“AST 过滤后就安全”的承诺；隔离依赖实际后端与宿主维护。Docker daemon 是可信控制面，容器配置/内核漏洞可能破坏隔离，因此首版定位为受管理的本地应用执行，未经额外评估不承诺面向敌对公网多租户的强隔离平台。[Docker 安全文档](https://docs.docker.com/engine/security/)。其他更深隔离后端仍后续；可选 E2B 已按 ADR-0016 单列 T-10C，其保证不从 Docker 参数推断。本轮仅文档调整，没有创建远端资源。

以下起始默认值已通过本机固定可信控制探针，尚未做性能、恶意超限或完整平台兼容实测；在 T-10A/T-10B 以真实边界检查继续确定，不回写历史 P4 fingerprint。内部 DockerLimits 严格有限，CPU 要求能表示为 100000µs period 下至少 1000µs 的整数 quota，memory 至少 6MiB；这不是运行并发/期限已接通的声明：

| SandboxLimits/固定控制 | 工作默认 | 规则 |
| --- | --- | --- |
| execution_seconds / control_seconds | 10s / 10s | 执行受工具/Run/推进剩余预算再收紧；停止/销毁用独立控制期限，超时不等于停止成功 |
| memory_bytes / cpus / pids | 512MiB / 1.0 / 64 | 有限硬配置及实际检查；memory+swap 总额不超过 512MiB；CPU 为配额而非权重；达到限制不盲猜成功 |
| workspace_bytes | 64MiB | 每操作新 /work tmpfs；/tmp 与 /dev/shm 另各限 16MiB，计入内存限额；不落宿主工作目录 |
| max_code_bytes / max_output_bytes | 64KiB / 64KiB | code UTF-8 且仍受 Limits.max_arguments_bytes；stdout+stderr 合计原始字节，边读边限，达到上限停止并标截断 |
| concurrency | 2 | 按 backend/Runtime 所有活跃与未清理租约计数；无无界队列；旧执行未核清仍占额，重启不重置为 0 |
| 自动代码重试 / 共享状态 | 0 / 无 | 模型新代码必须新 operation；每次独立容器/工作区，无持久 Notebook |

平台承诺分别核验：核心 CPython 3.11–3.14 的 Linux/macOS/Windows 矩阵不变；新增沙箱必交验收基线为 Linux Docker Engine/Linux 容器 + CPython 3.11–3.14。macOS/Windows 本地 Linux VM 后端须另外实际验证后才列入沙箱已支持平台，不从核心 SDK CI 推断；原生 Windows 容器、远程 Docker daemon 不在首期支持范围；E2B 可选范围独立见第 10 节，当前尚未实现。Docker 专项 CI 明确提供 daemon 与预装镜像，环境缺失导致未验收，不能用 skip 或协议替身关闭任务。

后端配置（非秘密连接引用、镜像 digest、固定控制、限额/执行入口版本）与 Agent/工具定义绑定并持久化检查，避免旧审批或恢复运行在新镜像/弱限制下。内部操作 payload 的新增执行/清理字段在 T-10A 明确版本和旧记录读取，必要 DDL/migration 在对应实现同轮处理，不对旧操作伪造执行事实。记录与保留语义见运行契约/维护策略，不在此另造状态机。

2026-10-14 本地资源准备证据见 [Docker/镜像准备验证](../validation/sandbox-environment-preparation.md)。当前 macOS Linux VM 的基础探针可运行，但不代表 SDK/完整隔离平台验收。该 daemon 默认 seccomp=unconfined；实际执行须显式使用固定可信 profile 并核实有效，profile 内容 hash 纳入既定安全控制指纹。控制 CLI 不应继承用户配置中的容器代理/环境注入，准备探针使用独立空 auths/proxies 配置；具体 SDK 落地由 T-10A 实施，不改变以上固定边界或提前创建 API。

### 9.1 受控文件与私有产物存储

采用显式 LocalArtifacts + SQLite 元数据。T-10A 第二批已装配 Runtime 的 owned/borrowed/factory 字节资源与 ready 输入/Run 授予；构造不读写，open(storage=已打开的 SQLiteStorage) 检查目录、双向配对及排他所有权。显式配对事务初始化 Artifact 扩展 v1 的两表/索引，metadata 增量 artifact_store_id/artifact_schema；records DDL/user_version=1 保持，已存在扩展的完整 DDL 必须匹配，普通核心库不启用该扩展。私有目录 POSIX 0700、文件 0600，其他平台控制仍未实施；不承诺已加密。不以逻辑文件名寻址宿主，不跟随链接。接收事务共同保存 ready 输入、Run/授予/去重/检查点；私有预留/阶段不等于 ready。输出最终链仍是可靠暂存 → 确认销毁 → 同事务发布 ready/工具结果，尚未实施，不能把进程崩溃测试写成物理断电保证。

字节层每批额外预留 16KiB 清单逻辑配额（清单编码硬上限同值），计入下表 owner/store 容量，空批也占额；原始字节与未完成预留跨重开保留。配额不等于底层文件系统 block/inode 物理限额。缺清单/未知项 fail closed；已开始写入的批次不自动重写或删除，受控恢复/回收仍待后续接入，见 ADR-0017。

| ArtifactLimits | 工作默认 | 约束 |
| --- | --- | --- |
| max_input_files / max_input_bytes | 8 / 合计 8MiB | start 与本次执行所选输入分别检查；按实际字节限，不先无限读取；已有引用也占本次输入额 |
| max_output_files / max_artifact_bytes / max_export_bytes | 8 / 单文件 8MiB / 合计 16MiB | 声明/实际文件数及传输均校验；先超限拒绝，不把文件缩成貌似完整结果；stdout 限额独立 |
| max_owner_bytes / max_store_bytes | 64MiB / 1GiB | 每 tenant/user/Agent 与整个 store 的持久容量；包含暂存/预留/ready，事务预留，拒绝无界积累；宿主可在有限管理员上限内显式调整 |

逻辑输入名是单个 POSIX 文件名；输出是 /work/outputs 下相对 POSIX 路径：每段 UTF-8 最多 255 bytes、总长最多 1024 bytes/8 段，NFC 规范化；拒绝空段、.、..、绝对路径、反斜杠、控制字符与规范化后的重复。宿主文件实际路径始终使用 ID，与 OS 文件名大小写无关。批准后复制到 /work/inputs/<artifact_id>/<name>，目录/文件及受保护祖先由可信控制方管理，代码用户不能改写/删除/chmod 或替换输入根目录；仅 outputs/scratch 等指定子区可写，不使用 bind mount。可信 bootstrap 可按固定入口复制字节并降权，但模型代码全程非 root、固定隔离与资源控制有效；只读实际效果须测试，普通 chmod 0444 而仍归代码用户或可替换父目录不算保证。

输出导出不能跟随 symlink/hardlink、设备/FIFO 或有活动写者；可信控制确认主代码退出，并冻结环境/遗留写者、确认文件稳定后，保持环境可读以校验导出；随后停止整个进程树并销毁。冻结不满足最终清理条件，不能以 pause 冒充销毁；若无法有界阻止替换竞态，就拒绝该文件集合。Docker 文件传输采用固定 argv/受控流；若读取 Docker tar，只按限制解析允许的普通文件条目，不在宿主 extractall，不导入档案中的路径/权限。PNG 校验真实格式/大小/可解码，预览设置像素上限 16M；MIME 与后缀不可信，HTML/SVG 默认附件、禁止自动内联执行。下载安全 Content-Disposition 与无脚本预览在 T-13/T-14 验收。

输入/输出清单与 hash、artifact schema/存储配对、限额/镜像及 runner 版本纳入保存的运行/审批绑定。第二批已保存输入 store ID/扩展版本/限额 hash，原 Run 继续须复核；审批与 runner 绑定待真实后端。字节不占工具 JSON 正文额度，引用/元数据仍进入结果/上下文预算；不能返回不完整 JSON。ArtifactResource/Ref/Info/Limits 与 Artifact 扩展 DDL 已生成；有文件的 ContextManifest 用 v2，新版兼容旧 v1，无文件保持 v1，v1 不接受 artifact 枚举。T-15 根据实际扩展/记录落实迁移、完整备份与回收。

HTTP 未消费上传暂存的起始 TTL 为 1h（T-13 实施），包含在 store/owner 容量中；只由持有排他锁的显式维护清理回收，不默认后台删除。已消费/活动租约/共享引用不按 TTL 删除。T-10A 的 SDK 文件随 start 接收，不创建独立公共上传服务或孤立 ready 输入。

## 10. 可选 E2B 后端（计划，T-10C）

按 [ADR-0016](../decisions/0016-sandbox-artifacts-and-backends.md) 独立实现 experimental extra e2b，核心不导入供应商 SDK，不要求云账号。SDK 精确版本/许可和模板 build、可用区域/资源规格在真实适配时核验并锁定；官方滚动文档 E-30 至 E-33 不是兼容性验收。当前没有该依赖、模板、账号或远端实验。

Backend 的通用责任是持久执行键/归属匹配、能力预检、单次执行、输入复制/有界输出传输、状态查询、停止/销毁证据；Docker SandboxLimits 的 seccomp/PID/tmpfs 等控制属于 Docker profile。E2B 使用明确的 provider profile（有限 timeout、memory/vCPU、文件/输出容量、并发等）与 capability 声明，逐项验证可用保证；任何要求的硬保证不能提供则启动前拒绝该 profile，不把仅轮询的软件限制冒充内核限额。T-10C 以符合已选安全 profile 的真实模板为验收条件；不能为了上线放宽文件/授权/派发/清理规则。

宿主显式配置 credential_ref、template/build、region、cloud_data_policy、有限并发/寿命和费用上限；凭据仅宿主解析，不传入环境/文件/模型，不读取或输出密钥值。默认 profile 禁代码外连、限制公共服务访问，实际选定 SDK 对应设置和黑盒测试均要有证据，不能继承供应商默认。用户明确准许上传的合成代码/文件才经 provider 控制 API 传输；费用包括创建/运行/清理/失败与剩余活动资源，单独账本，不挪用模型预算。

每 operation 新资源，模板预装 Python/分析依赖；不提供 persistent context、pause/fork 或 Shell API。持久 metadata 匹配 execution_key 与归属，实际 resource ID 仅内部保存；创建应答丢失不再次 create，查询/kill/期限/已过期资源的证据按运行契约处理。费用未知或预算不足停止新付费派发，并保留已有资源必要收尾的预算/义务；不能以停止收费请求代替已销毁。公开提供 adapter 前必须有真实远端单次执行/文件/网络/权限/应答丢失/取消/崩溃恢复/最终资源无残留及安装证据。缺资源只关闭已验收子范围，Docker 必交链继续。

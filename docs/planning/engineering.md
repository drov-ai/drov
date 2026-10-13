# 首版工程方案

状态：T-01 已建立包、依赖锁与检查配置；运行内核和适配器仍未实现。实际安装范围见 [T-01 验证](../validation/t01-engineering.md)。更新：2026-10-13。

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

依据：[E-09 至 E-23](evidence.md)。T-01 已解析并在仓库生成精确 `uv.lock`，安装检查不等于运行时集成兼容；Git 尚未提交。T-14 再生成前端锁文件。库的依赖下界从实际安装的版本开始，范围内其他版本仍需后续兼容检查；锁文件用于开发/CI，用户安装库不会自动继承开发锁。后续适配任务补充最低版本与锁定版本的协议/行为测试，不能只测试维护者电脑。

## 2. 一个 Python 分发包，按需安装

Python 导入名使用 `drov`；PyPI 分发名暂定 `drov`，占用和控制权待发布准备核实。若不可用，变更分发名不自动变更导入名。npm 组织保留，v0.1 不为凑产品线发布 JS SDK。

T-01 已声明 extras：`openai`、`mcp`、`server`，目前只安装依赖，没有对应 Drov 适配器。核心依赖为 Pydantic、aiosqlite、portalocker、typing-extensions；目标是 SQLite 开箱可用，无需额外数据库服务，存储实现从 T-03 开始。extras 只选择依赖，不生成不同内容的 wheel；T-14 后统一 wheel 携带预构建工作台静态文件，`server` 增加运行 HTTP 服务所需依赖，SDK-only 导入不加载它们。开发依赖放 dev group，文档/评测组在对应任务引入，不成为 SDK 必装依赖。首版不拆多个协同发版的 Python 包。

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
  integrations/mcp/           可选 MCP 客户端
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

应用输入首版为非空文本；多模态和任意消息列表入口留到后续。工具参数支持 JSON 标量、有限枚举、list、嵌套 dataclass/Pydantic BaseModel、可空类型；拒绝 Any、位置专用参数、变长参数、未解析引用和任意 Python 对象。ToolContext 按类型注入，不能出现在模型 schema。

工具返回可以是可序列化的上述类型及字符串键 dict；返回 schema 与调用参数 schema 分开。`output_type` 首版支持 str 或根为对象的 dataclass/BaseModel；嵌套类型遵循上述子集，不承诺任意 Pydantic schema 都能发给供应商。解析后本地校验，拒绝多余字段与 NaN/Infinity；不静默把字符串转为数字。首版拒绝 datetime/bytes/文件句柄等非 JSON 类型，应用自行显式转换。T-02 明确拒绝递归、RootModel、alias、自定义 validator/serializer/构造钩子、自定义 schema 和嵌套 Annotated 约束；常见 Field 数值/长度/字符串正则约束会保留并验证。普通对象额外字段一律拒绝。详见 [ADR-0006](../decisions/0006-python-contracts.md)，复杂类型不得静默降级。

注册时生成并校验 schema。供应商严格模式导出时拒绝不支持的结构，不降级成宽松 JSON；可将有默认值的参数导出为必须显式提供的字段，并在说明中展示默认值，但不能把 null 偷换成默认值。Nullable 才接受 null。供应商拒绝、输出截断、结构不合法分别报告，不自动调用另一模型“修复”。

| 扩展边界 | 最小接口责任 | v0.1 支持承诺 |
| --- | --- | --- |
| ModelAdapter | capabilities；生成归一化流；完整响应/工具计划/用量；关闭所有权 | 支持应用自建适配器；需通过公共契约测试 |
| Storage | 原子事务、条件更新、作用域查询、版本读取、所有权 | 内存与 SQLite；协议暂为内部，不承诺外部数据库实现兼容 |
| Tool | schema、版本、effect、approval、执行；可选结果 verifier | Python 工具和 MCP 桥接；verifier 只读且有超时 |
| AuthAdapter / Authorizer | 解析可信身份；按 actor/action/resource 判断访问和控制权 | 应用可注入异步接口；HTTP 用户数据不能直接构造身份 |
| Memory | 首版策略描述 + 内置事务处理器 | 不开放任意外部写入后端冒用内置原子性 |
| 观测 | 只读事件订阅、元数据导出 | 本地查看/JSON 导出；OTel 后续，不放任回调改写状态 |

模型协议首版最小归一结果含 message、tool_calls、usage、provider_items；后者是需要往返的供应商协议数据，不是可展示的内部推理。流式增量必须以完整响应事件收尾才可提交工具计划。T-02 已实现 drov.models.protocol 与离线 ScriptedAdapter，生成 [schema](../reference/schema-v1.json)。ModelRequest 包含 messages/tools/output_schema/provider_items，ModelResponse 包含 message/tool_calls/usage/provider_items/finish_reason；generate 产出 delta/complete，aclose 为异步关闭。collect_response 拒绝缺少 complete、complete 后数据、重复 call_id 或不完整工具计划；不调用真实供应商。Authorizer 为 authorize(actor, action, resource)，AuthAdapter 为 authenticate(credential)，实际权限引擎及服务认证尚未实现。

## 4. SQLite、锁与 Memory

SQLite 启动要求运行库版本至少 3.35；检测实际 `sqlite3.sqlite_version`，不能只检查 Python 版本。采用 `journal_mode=DELETE`、`synchronous=FULL`、`foreign_keys=ON`、`busy_timeout=5000`，读取验证设置结果。选择回滚日志是首期单连接下的简化；WAL、多连接及网络文件系统不在支持范围。[E-15](evidence.md) 仅解释 SQLite 机制，不能替代进程故障实验。

所有 SQL 经同一连接；Runtime 事务调度锁包住完整的 BEGIN IMMEDIATE 到 COMMIT/ROLLBACK，普通查询也不得插入他人的事务中。aiosqlite 的逐条请求队列不等于业务事务隔离。取消等待提交时必须等数据库工作完成、确认提交或回滚后才释放锁；数据库状态仍不明则停止派发并保留所有权，不让另一协程继续使用不明事务。

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

T-02 的 Limits 字段名和配置 schema 由 [Python 类型](../../src/drov/config.py) / [生成文档](../reference/schema-v1.json) 给出。max_input_tokens 默认 None：已知窗口在 T-07 取 min(16384, context_window - output_reserve_tokens)，未知窗口必须由应用显式给上限，Agent 定义已前置校验。None 不是取消默认安全预算。write_tool_retries 只接受整数 0；上表默认值是工程决定，不是性能承诺。

## 7. 服务和工作台

首版单 worker；多 worker/自动热重载对同一数据库的竞争必须产生明确 StorageInUse，不能绕开锁启动。SDK、HTTP、UI 的身份、权限、限额和状态走同一 Runtime。

默认只有显式本地模式：绑定 127.0.0.1，随机本次启动令牌，检查 Host/Origin，工作台同源；浏览器用认证 fetch 订阅 SSE，不把令牌放 URL、持久日志或跨站请求中。首版不接任意 Origin。非本地绑定要求应用提供 AuthAdapter、Authorizer 和允许来源，否则启动失败。传输 TLS、账号系统与凭据轮换由宿主接入，Drov 不宣称自带完整 IAM。

工作台必须能：启动三个样例、列会话和运行、查看状态/事件/上下文来源与裁剪原因、展示工具参数、提交审批/澄清/核查、取消、重连和查看错误。它是本地开发界面；无工作流画布、远程组织管理、后台账号体系。UI 首版标记实验性，仍必须通过上述验收，不可用标签代替测试。

## 8. 实施前与实施中分界

T-01 已验证锁定依赖的本地安装组合，T-02 已验证类型、工具 schema 与归一模型协议；各集成行为仍由对应任务验证。T-01/T-02 负责把这些决定变成可导入的类型、锁文件和有意义的契约测试；发现版本不兼容时记录差异，不能偷偷切换架构。最低 Python、范围或语义变化需要同步 ADR 和验收。缺少真实 Key、发布账号或最终域名不阻塞这些任务。

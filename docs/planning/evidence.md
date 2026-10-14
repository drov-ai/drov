# 证据与假设登记

原 P1–P3 查阅日期：2026-10-12（Asia/Shanghai）；新增 Docker、AgentScope Workspace 与 E2B 官方资料于 2026-10-14 查阅。P1 核对竞品，P2 补充并发语义，P3 核对工程与发布官方资料；本页文档审查不代表安装/运行竞品、调用模型或体验实验。

## 来源

| 标识 | 来源与版本 | 本轮可支持的事实 | 使用边界 |
| --- | --- | --- | --- |
| E-01 | [Agent 概览，2.0.9](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/agent/overview) | 提供 reply、reply_stream、结构化输出、上下文和中断相关能力 | 不由功能列表推断具体恢复保证或任务成功率 |
| E-02 | [长期记忆，2.0.9](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/long-term-memory) | 有 Agentic Memory、ReMe、Mem0；Agentic Memory 的异步检索存在注入时序；ReMe SDK 示例显式关闭 middleware | 不推导为所有 Memory 实现都有相同延迟行为，也不声称整个框架不管理资源 |
| E-03 | [Middleware，2.0.9](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/middleware) | 有生命周期钩子及 OpenTelemetry Tracing；示例配置 provider/exporter；预算 middleware 以 reply 为范围 | 不声称 AgentScope 无可观测能力，不将 reply 预算当成全局硬费用上限 |
| E-04 | [应用服务架构，2.0.9](https://docs.agentscope.io/en/versions/2.0.9/deploy/agent-service) | 服务覆盖会话、权限交互、调度、持久化及协议适配；完整示例使用 Redis、Python 后端和 React 前端；认证提供可替换占位依赖 | 这些是服务示例要求，不是使用 SDK 的必备条件；不据此判定生产可靠性 |
| E-05 | [官方仓库 README，main](https://github.com/agentscope-ai/agentscope) | 列出工具/MCP/skills、模型、权限、Memory、工作区、多 Agent 及服务能力 | main 可变化；属于补充导航，不作为固定版本所有功能的证明 |
| E-06 | [Agent 配置，2.0.9](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/agent/configure-agent) | 可用于后续对照模型、工具与配置接口 | 尚未做逐字段 API 兼容性审计 |
| E-07 | [Python asyncio task 文档，/3 入口](https://docs.python.org/3/library/asyncio-task.html) | shield 可让调用者取消不直接取消被保护任务；运行任务需要保留引用 | 本次页面标注 3.15.0；用于设计依据，不代表选定最低 Python；P3 要按选定版本复核 |
| E-08 | [Python futures 文档，/3 入口](https://docs.python.org/3/library/concurrent.futures.html) | 已开始执行的 Future 不能靠 cancel 保证终止 | 同为当前 /3 文档；不能由 Future 状态推断远端副作用是否撤回 |
| E-09 | [asyncio，Python 3.11](https://docs.python.org/3.11/library/asyncio-task.html) / [futures，Python 3.11](https://docs.python.org/3.11/library/concurrent.futures.html) | 3.11 有 timeout/TaskGroup；shield 与任务引用、已运行 Future 取消限制可在选定最低版本查证 | 按 3.11 页面复核 E-07/08，不证明 Drov 生命周期实现正确 |
| E-10 | [Pydantic TypeAdapter，latest 文档](https://pydantic.dev/docs/validation/latest/concepts/type_adapter/) | TypeAdapter 支持验证、序列化和 schema 生成，包括 dataclass 等类型 | Drov 选择 2.x，子集由自身接口约束；不等于供应商支持所有生成 schema |
| E-11 | [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create) | 工具调用与最终结构化输出有各自协议；strict 有 schema 限制；Responses 有 store、协议往返和截断选项 | 可变官方文档；使用 OpenAI Docs 检索并读取。未核验用户账号/model 权限，不默认选择模型或宣称零保留；SDK 精确版本与选项待适配测试 |
| E-12 | [官方 MCP Python SDK，v2 文档入口](https://py.sdk.modelcontextprotocol.io/) | 页面标注 v2 为当前稳定线、要求 Python 3.11+，覆盖客户端与多种传输 | 非固定 patch 版本；Drov 只选 stdio tools 子集，实际锁与协商版本在 T-12 验证 |
| E-13 | [MCP transports，2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | stdio 是客户端启动子进程的标准传输，另有 Streamable HTTP | 固定规范入口不等于实现全部协议；Drov 服务 SSE 不能冒充远程 MCP 支持 |
| E-14 | [aiosqlite，stable](https://aiosqlite.omnilib.dev/en/stable/) | 每连接共享工作线程/请求队列，异步代理 SQLite 操作 | 请求队列不自动形成 Drov 事务隔离；锁、取消和 COMMIT 仍需自行测试 |
| E-15 | [SQLite PRAGMA](https://www.sqlite.org/pragma.html)、[WAL 文档](https://www.sqlite.org/wal.html) | 官方说明 journal_mode/synchronous；WAL 有拓扑条件，当前页亦列 WAL-reset bug 和修复版本 | 首版选择单连接 DELETE 回滚日志；不将该选择宣传为不会丢数据或完整可靠性证明 |
| E-16 | [Portalocker 官方文档，latest](https://portalocker.readthedocs.io/en/latest/) | 提供跨平台锁，Unix 文件锁具有协作性质，平台/文件系统有限制 | 不宣称强制阻止外部数据库修改；实际跨平台排他和崩溃释放需测试 |
| E-17 | [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) | lifespan 可安排启动和关闭资源 | 不证明 Drov 自有/借用资源或多 worker 语义已经正确 |
| E-18 | [uv 项目结构](https://docs.astral.sh/uv/concepts/projects/layout/)、[锁与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、[Hatch 构建](https://hatch.pypa.io/latest/config/build/) | 提供项目/锁文件管理与构建文件包含机制 | 锁需实际解析；wheel UI 资产与最低依赖需安装验证 |
| E-19 | [pytest fixtures，stable](https://docs.pytest.org/en/stable/how-to/fixtures.html) | 可构造分层夹具与清理流程 | 不证明本项目已有测试；故障矩阵/质量阈值为工作设计 |
| E-20 | [MkDocs 部署](https://www.mkdocs.org/user-guide/deploying-your-docs/)、[Cloudflare Pages 构建配置](https://developers.cloudflare.com/pages/configuration/build-configuration/) | 文档可构建为静态产物，Pages 配置构建与输出目录 | 未配置站点/域名/DNS/权限；静态托管不运行 Agent 后端 |
| E-21 | [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/) | 可用 OIDC 身份进行可信发布配置 | 不证明 drov 分发名可用或用户已配置发布身份 |
| E-22 | [Apache License 2.0 官方文本](https://www.apache.org/licenses/LICENSE-2.0) | 给出使用/分发条件、声明保留及相关条款 | Drov 自有代码的工作许可选择；不自动决定第三方代码/品牌素材许可 |
| E-23 | [Vite 入门](https://vite.dev/guide/)、[Node.js 版本](https://nodejs.org/en/about/previous-releases) | Vite 支持 vanilla-ts 和静态构建；Node 官方维护发布线 | 前端锁和 Node patch 在 T-14 固定；终端用户不因此需要 Node |

固定版本文档比 `latest` 更适合设计对照，但网页仍可能更新。需要公开复现竞品行为时，另记录安装版本、提交标识、环境和实验配置。此次未下载或复制上游源码。Drov 状态机、Memory、默认值、阈值和维护规则属于自己的工作设计，详细见 ADR-0002/0003/0004；不是从来源推导出的“已经实现”。精确 patch 与组合兼容性留给实际解析/测试，不能把官方单库说明当作集成验证。

## 本项目的事实

- 用户确认 Python 为主要语言。
- 用户明确希望通用框架，能力范围对标 AgentScope 2.0，并撤回特定业务优先的回答。
- 工作目录初始只有品牌素材，没有框架代码。
- 用户提供 GitHub 组织 drov-ai、npm 组织 drov 和域名 drop.dev；此次没有核验其账号控制权、包名可用性或 DNS 状态。
- P1–P3 当时只交付规划；当前实现与验收以 progress 为准，尚无公开 SDK 发布、线上服务、竞品性能分数或用户采用数据。
- 2026-10-14 用户明确确认首版具备“生成并执行代码”；沙箱 SDK 尚未实现，实际基础容器资源准备见 [记录](../validation/sandbox-environment-preparation.md)，不等于 T-10A/T-10B 验收。

## 设计假设的证据要求

H-01 至 H-04 的定义在 [产品定义](product.md)。所有假设当前均为 **未验证**。

| 假设 | 需要补充的材料 | 不能替代验证的材料 |
| --- | --- | --- |
| H-01 API 更容易接入 | 相同场景、明确起点的使用记录和配置对比 | 仅比较一段精简示例的行数 |
| H-02 入口一致更易服务化 | SDK/HTTP 相同规则下的行为测试与迁移记录 | 两个入口使用相同函数名 |
| H-03 上下文检查更易排错 | 固定故障案例、原因定位和修复过程 | 看起来复杂的 Trace 页面 |
| H-04 本地启动更方便 | 干净环境、完整安装启动路径和使用反馈 | 维护者自己的已配置电脑上成功 |

## 后续新增事实的格式

记录结论、来源 URL、版本或提交、查阅日期、证据类型（文档/源码/实测）和限制。修正事实时保留修正原因；不要悄悄把假设改写为已证实优势。

## 2026-10-14 沙箱机制补充来源

类型：官方文档；全部为滚动文档，非固定 Engine/CLI 发布版本。此次读取用于工程工作选择，没有运行容器、选定镜像 digest、复制第三方代码或作竞品沙箱实测。固定版本/平台与实际隔离效果在 T-10A/T-10B 登记，不能由网页机制推断实现通过。

| 标识 | 来源与查阅日期 | 可支持的机制事实 | 使用边界 |
| --- | --- | --- | --- |
| E-24 | [Docker run](https://docs.docker.com/engine/containers/run/) / [CLI 参数](https://docs.docker.com/reference/cli/docker/container/run/)，2026-10-14 | 可固定 image digest，配置用户、只读文件系统、PID 限额、日志与挂载/权限 | 本机 CLI/Engine/镜像兼容性未验证；安全配置是 Drov 工作决定 |
| E-25 | [资源限制](https://docs.docker.com/engine/containers/resource_constraints/)，2026-10-14 | 内存/swap、CPU 及内核能力会影响实际限制 | 不能只看命令参数判实际有界；须进行限额探针 |
| E-26 | [Docker 安全](https://docs.docker.com/engine/security/) / [seccomp](https://docs.docker.com/engine/security/seccomp/)，2026-10-14 | daemon 与内核是信任边界，seccomp 依赖实际内核支持 | 不承诺容器抵抗所有漏洞/敌对多租户；Drov 固定策略需实测 |
| E-27 | [none 网络](https://docs.docker.com/engine/network/drivers/none/)，2026-10-14 | none 模式仅配置 loopback，供网络隔离选择 | 仍检查实际配置/无端口发布，并以合成监听证明无外连 |
| E-28 | [tmpfs](https://docs.docker.com/engine/storage/tmpfs/)，2026-10-14 | 临时内存文件系统与大小控制，存在宿主 swap 条件 | 限额、swap 与实际工作空间边界须验证；不声明物理擦除保证 |

## 2026-10-14 首期沙箱范围审查补充

由用户在 T-10A 前提出范围/E2B 问题后核对，见 [范围审查](../validation/sandbox-scope-review.md)。只补外部事实和建议依据，没有改变已确认 C-17 或任务/发布门槛，未创建云资源或调用模型。以下来源查阅日期均为 2026-10-14。

| 标识 | 来源与版本 | 可支持的事实 | 使用边界 |
| --- | --- | --- | --- |
| E-29 | AgentScope Python **v2.0.9** 的 [Workspace 概览](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/workspace/overview)、[使用](https://docs.agentscope.io/en/versions/2.0.9/building-blocks/workspace/run-workspace)；固定 commit `2e706a6fa66813646e0d267e13757b5dbe912359` 的 [E2BBackend](https://github.com/agentscope-ai/agentscope/blob/2e706a6fa66813646e0d267e13757b5dbe912359/src/agentscope/workspace/_e2b/_e2b_backend.py) / [E2BWorkspace](https://github.com/agentscope-ai/agentscope/blob/2e706a6fa66813646e0d267e13757b5dbe912359/src/agentscope/workspace/_e2b/_e2b_workspace.py) | Workspace 包含多后端、文件/命令工具、MCP/Skill 与上下文卸载；E2B 通过 metadata 重连，关闭采用 pause 保留工作区 | GitHub tag/commit 实际核对；不混用 Java/main/独立 Runtime。未运行，不比较隔离强度、任务质量或所有恢复保证 |
| E-30 | E2B [概览](https://docs.e2b.dev/)、[代码分析](https://docs.e2b.dev/code-interpreting/analyze-data-with-ai)、[Code contexts](https://docs.e2b.dev/code-interpreting/contexts)，滚动官方文档 | 托管 Linux VM、文件输入和代码执行的文本/图表等结果、可管理代码上下文 | 未锁 SDK/模板/构建版本或安装；云机制不等于 Drov 的权限/产物持久化已支持 |
| E-31 | E2B [网络](https://docs.e2b.dev/network/internet-access) / [限制公共访问](https://docs.e2b.dev/network/restrict-public-access)，滚动官方文档 | 外连默认开启，可配置关闭/规则；公共访问与代码解释器访问须按 API 收紧 | Drov 若接入要显式配置并实测，不能从“用了 E2B”推断满足现有禁联网/授权边界 |
| E-32 | E2B [生命周期](https://docs.e2b.dev/sandbox)、[pause/resume](https://docs.e2b.dev/sandbox/persistence)、[metadata](https://docs.e2b.dev/sandbox/metadata)，滚动官方文档 | ID/元数据查询、kill、暂停/恢复文件系统与内存状态等机制 | metadata 不证明唯一创建，客户端超时不证明已停止；是否满足原执行键/丢应答/取消恢复必须独立验收 |
| E-33 | E2B [计费/额度](https://docs.e2b.dev/billing)，滚动官方文档 | 用量计费、计划/账户限制、可设支出上限 | 不假设用户享有赠额或批准支出；真实接入先核实账户/模板/实际价格及独立沙箱预算，本轮无费用实验 |

2026-10-14 范围采纳补记：用户采纳 E-29 至 E-33 支撑的具体审查建议，形成 [ADR-0016](../decisions/0016-sandbox-artifacts-and-backends.md)。受控文件必交/E2B 可选是 Drov 的工作决定，不是官方来源证明的 Drov 已支持能力；本次不追加未经验证的 SDK 版本、模板/digest 或远端实验。

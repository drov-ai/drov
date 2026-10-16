# Drov

面向 Python 开发者的通用 Agent 框架，目标是以少量核心概念提供完整、可组合的能力，并让开发、调试和服务化使用一致的运行机制。

**开发版本 `0.1.0a0`；T-10A/T-10B 的 Docker Python、文件与故障专项已验收。** Runtime 支持受控文件输入、代码审批、实际单次执行、CSV/PNG 持久产物与授权下载。完整任务状态和下一项以 [progress](docs/planning/progress.md) 为准；受测隔离/资源/SDK硬终止、原生Linux3.11–3.14 Docker专项与安装证据见 [T-10B 验证](docs/validation/t10b-sandbox-faults.md)。包另提供模型协议、事务存储、工具/权限、Agent 循环、上下文/Memory 和持久交互/恢复。OpenAI Responses 已实现离线协议适配，MCP stdio已接通（实际验收见progress）；HTTP按ADR-0025接通（验收见progress），实验性[本地工作台](workbench/README.md)已接入同一HTTP；尚未公开发布包；DeepSeek真实smoke已运行并保留失败，完整模型质量尚未验收，见[T-16记录](docs/validation/t16-evaluation.md)。

P4 的设计决定保存在 [实施基线](docs/planning/implementation-baseline.md)。当前开发入口与限制见 [开发说明](docs/development.md)，可导入的定义见 [类型与 schema](docs/reference/README.md)，实际检查见 [T-08 验证记录](docs/validation/t08-memory.md)。本轮证据见 [T-09 子范围验证](docs/validation/t09-interactions.md)，完整证据见 [核查/恢复验证](docs/validation/t09-reconciliation-recovery.md)，T-10 已验收，见 [专项验证](docs/validation/t10-faults-cancellation.md)；下一项及资源准备见 progress；换会话方法见 [交接说明](docs/handoff.md)。

## 本地开发

使用 Python 3.11+ 和 uv 0.13.0，在仓库根目录执行；uv 的隔离安装方式见开发说明。

```sh
uv sync --locked
uv run --locked python -c "import drov; print(drov.__version__)"
uv run --locked pytest
```

构建和干净环境安装验证：

```sh
uv run --locked python scripts/check_package.py
uv run --locked twine check --strict dist/*.whl dist/*.tar.gz
```

可直接运行 [离线检索示例](examples/README.md)，使用真实本地检索工具、预设 ScriptedModel 和严格 Report 输出：

```sh
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

预设回答只验证运行契约，不证明模型推理质量。外部 Python 工具已支持 read/write/unknown；内置 Memory 使用专用事务工具与限时检索。显式 Runtime 的 ASK 保存审批并支持原子响应，临时 Agent.run 仍 failed/interaction_unavailable；外部 write/unknown 单次派发，未知效果须显式核查，不承诺外部 exactly-once。上下文/记忆来源可由 get_context 核对。

这些操作不需要 API Key。构件生成在本地 `dist/`，不会发布到 PyPI。openai extra 已提供 [Responses 离线适配](docs/decisions/0022-openai-responses-adapter.md)；mcp extra已提供 [本地stdio适配](docs/decisions/0024-mcp-stdio-tools.md)；server提供 [HTTP/身份/SSE与文件上传下载](docs/decisions/0025-http-identity-and-uploads.md)，可运行`examples/http_offline.py`及`--stream`例。

官方客户端的本地 HTTP/SSE 示例（不读取真实凭据）：

```sh
uv run --locked --extra openai python examples/openai_responses_offline.py
uv run --locked --extra openai python examples/openai_responses_offline.py --stream
```

## 项目方向

- 能力范围对标 AgentScope 2.0，覆盖工具、MCP、Memory、权限、Human-in-the-loop、Tracing、持久化与应用服务。
- 面向多种 Agent 应用；官方示例用于验证通用性，不限定产品业务范围。
- 优先验证三项设计主张：渐进式 Python API、跨入口一致的运行机制、可检查的上下文与记忆。
- Docker Python/受控文件/持久产物已有真实离线 SDK [示例](examples/sandbox_offline.py) 与 [验证](docs/validation/t10a-runtime-sandbox.md)，需显式配置 Docker 和固定镜像。可选 E2B adapter 已接入相同 Runtime，实际验证及限制见 [T-10C](docs/validation/t10c-e2b.md) 与 [ADR-0023](docs/decisions/0023-e2b-single-execution.md)；范围见 [ADR-0016](docs/decisions/0016-sandbox-artifacts-and-backends.md)，当前状态见 progress。
- 本地工作台用于呈现上述能力；复杂集成按需安装。

以上是产品目标，不代表已经实现，也不代表已证明优于其他框架。

## 从这里开始

| 文档 | 用途 |
| --- | --- |
| [规划导航](docs/planning/README.md) | 阅读顺序、文档职责和实施前阶段安排 |
| [当前进度与下一轮](docs/planning/progress.md) | 已完成的工作、待解决问题、下一轮交接 |
| [开发说明](docs/development.md) / [T-01 验证](docs/validation/t01-engineering.md) | 安装、检查、构建方式与实际验证范围 |
| [P4 实施基线](docs/planning/implementation-baseline.md) | 代码就绪审查、文档指纹与 T-01 交接 |
| [产品定义](docs/planning/product.md) | 目标、原则、边界与差异化假设 |
| [能力对标表](docs/planning/capability-matrix.md) | AgentScope 基线、首期/实验性/后续范围、验收依据 |
| [公共 API 草案](docs/planning/api-draft.md) | 调用语义、接口字段、schema 支持范围 |
| [架构与模块边界](docs/planning/architecture.md) | 统一内核、数据归属、资源所有权与组合边界 |
| [运行行为契约](docs/planning/runtime-contract.md) | 状态机、审批、恢复、Memory 时序与故障处理 |
| [三类参考应用](docs/planning/reference-scenarios.md) | 使用片段、预期行为与验证案例 |
| [首版工程方案](docs/planning/engineering.md) | 技术栈、包结构、依赖边界、默认限额 |
| [质量与评估计划](docs/planning/quality-plan.md) | 离线/集成/故障/真实模型及体验验收 |
| [分期实施任务](docs/planning/implementation-plan.md) | 21 张任务卡（含必交 T-10A/T-10B、可选 T-10C）、前置依赖、交付与完成条件 |
| [数据与维护发布](docs/planning/release-policy.md) | 数据、迁移、许可、发布、官网和准备材料 |
| [交付与就绪标准](docs/planning/delivery-plan.md) | 规划轮次、开发里程碑与进入代码阶段的条件 |
| [证据与假设](docs/planning/evidence.md) | 外部事实、来源、版本及待验证判断 |
| [项目方向决策](docs/decisions/0001-project-direction.md) | 已确认方向及其原因 |
| [统一运行内核决策](docs/decisions/0002-unified-runtime.md) | 入口一致、步骤恢复和执行边界的取舍 |
| [上下文与记忆决策](docs/decisions/0003-context-and-memory.md) | 可检查输入、检索策略与事务提交的取舍 |
| [首版工程决策](docs/decisions/0004-first-release-engineering.md) | 首期范围、依赖和维护规则的取舍 |
| [控制与查看决策](docs/decisions/0005-control-and-observation.md) | 原子响应、正文权限、历史与核查收尾的取舍 |
| [协作规则](AGENTS.md) | 后续每轮必须遵守的工作方式 |

## 品牌与发布

现有素材位于 [drov-brand](drov-brand/README.md)。GitHub 组织为 [drov-ai](https://github.com/drov-ai)，npm 组织为用户提供的 [drov](https://www.npmjs.com/org/drov)。Python 核心拟通过 PyPI 发布，分发包名与权限尚待核验。自有代码/文档采用 [Apache-2.0](LICENSE)，第三方来源与品牌边界见 [THIRD_PARTY](THIRD_PARTY.md)；现有品牌文件未改动，也未打入 Python 构件。

用户提供的域名为 `drop.dev`；是否为最终官网域名待核实。当前不提供官网上线、安装命令或发布状态声明。

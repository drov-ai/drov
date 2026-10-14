# 实施前规划导航

实施状态、下一项与用户准备统一见 [progress](progress.md)。T-09 全路径证据与 T-10 硬终止/故障矩阵分别维护，首版新增 Python 代码沙箱见 ADR-0013 与 T-10A/T-10B。日期：2026-10-14（Asia/Shanghai）。

## 当前共识

Drov 面向通用 Agent 应用，以 Python 为主要语言、AgentScope 2.0 为主要能力基线。用户已撤回“先以业务系统助手为产品方向”的回答；不得恢复该限制。用户要求在进入框架代码实施前，逐轮形成文档、规则和共同目标，并在每轮结束说明下一轮建议。

## 阅读顺序与唯一职责

| 顺序 | 文档 | 负责回答 |
| --- | --- | --- |
| 1 | [进度](progress.md) | 目前做到了哪里，这轮该做什么？ |
| 1a | [实施基线](implementation-baseline.md) | 为什么可以开始代码，T-01 怎样交接，基线怎样留存？ |
| 2 | [产品定义](product.md) | 为谁构建、覆盖什么、设计主张是什么？ |
| 3 | [能力表](capability-matrix.md) | 哪些首期必交、实验性或后续，怎样验证？ |
| 4 | [架构](architecture.md) | 模块、数据和资源分别由谁负责？ |
| 5 | [行为契约](runtime-contract.md) | 正常、暂停、恢复和失败时确切发生什么？ |
| 6 | [API 草案](api-draft.md) | 用户如何使用，接口的含义是什么？ |
| 7 | [参考应用](reference-scenarios.md) | 不同场景是否能使用同一套抽象？ |
| 8 | [工程方案](engineering.md) | 用什么技术、目录、支持矩阵与默认值？ |
| 9 | [质量计划](quality-plan.md) | 用哪些夹具、断言、真实评测与报告检验实现？ |
| 10 | [实施任务](implementation-plan.md) | 先做什么、依赖什么、怎样完成一项？ |
| 11 | [维护发布策略](release-policy.md) | 数据/兼容/复用/版本如何维护，需要用户准备什么？ |
| 12 | [交付计划](delivery-plan.md) | 还需哪些设计，何时开始代码？ |
| 13 | [证据](evidence.md) | 对外部事实和优势假设有什么依据？ |
| 14 | [方向决策](../decisions/0001-project-direction.md) | 哪些产品方向已确认？ |
| 15 | [运行内核](../decisions/0002-unified-runtime.md) / [上下文记忆](../decisions/0003-context-and-memory.md) / [首版工程](../decisions/0004-first-release-engineering.md) / [控制与查看](../decisions/0005-control-and-observation.md) | 采用了什么工作设计，有什么代价？ |
| 16 | [Python 类型契约](../decisions/0006-python-contracts.md) / [已实现定义](../reference/README.md) / [会话交接](../handoff.md) | 类型子集怎样落地，换会话怎样恢复实施？ |
| 17 | [事务存储决定](../decisions/0007-transactional-storage.md) | 存储、去重、取消与所有权怎样保持一致？ |
| 18 | [注册与所有权决定](../decisions/0008-runtime-ownership.md) | 指纹、任务及资源生命周期怎样落地？ |
| 19 | [Python 工具决定](../decisions/0009-python-tools.md) | 权限、队列与事务派发怎样落地？ |
| 20 | [只读闭环决定](../decisions/0010-read-only-agent-loop.md) | 接收、模型循环、历史、预算和授权事件如何落地？ |
| 21 | [上下文装配决定](../decisions/0011-context-assembly.md) | 完整轮次、预览、清单与授权查询怎样落地？ |
| 22 | [事务 Memory 决定](../decisions/0012-transactional-memory.md) | 内置工具、作用域、限时检索与快照怎样落地？ |
| 23 | [首版代码沙箱决定](../decisions/0013-first-release-code-sandbox.md) | 新增首版范围、后端/权限/清理边界与任务怎样安排（计划）？ |

## 状态词约定

- **已确认方向**：来自用户明确要求或已接受的产品建议。
- **设计提案**：当前推荐方案，可以在评审中修改。
- **工作决定**：已经选择的设计，用于后续实施；是否已实现另列，不从决定推断功能状态。
- **实施基线**：经 P4 一致性审查、足够开始编码的源契约集合；变更需按影响更新 ADR/验收，并非永久冻结或已经运行验证。
- **待决策**：缺少具体契约或尚需比较方案，不等于必须询问用户。
- **未实现**：仓库没有交付对应运行能力。
- **已验证**：必须紧邻说明验证对象、方法和范围。

P1 至 P4 文档完成只表示设计和实施准备通过审查，不表示 API、架构、依赖组合或功能经过运行验证。

## 可追溯关系

```text
产品目标 G → 能力 C → 参考应用 S / 跨入口 X / 行为规则 B → 实现任务 T → 验证记录
                 ↑
       外部证据 E / 差异化假设 H
```

G、C、S、X、B、E、H 标识在对应源文档定义。B-01 至 B-17 是运行测试要求，按对应任务逐项验收；B-14 已延后。T-01 至 T-18 及 T-10A/T-10B 共 20 张卡在实施任务中定义，当前 T-01 至 T-08 本地验收完成、T-09 完整验收；状态在 progress，实际证据见 [T-09 核查/恢复验证](../validation/t09-reconciliation-recovery.md) 与 [T-10 故障验证](../validation/t10-faults-cancellation.md)。新会话方法见 [交接说明](../handoff.md)。质量计划建立映射，不另改需求含义。

## 规划边界

计划用四个有明确出口的阶段完成实施准备：P1 产品与接口草案、P2 架构与行为契约、P3 质量与分期任务、P4 一致性复核与实施基线。阶段可按工作量合并或拆分，不为凑轮次增加文件。

在后续阶段细化时优先更新这些文档；只有出现独立维护责任时才拆分新文件。完整就绪标准见 [交付计划](delivery-plan.md)。

T-08 内置 Memory 的落地决定见 [ADR-0012](../decisions/0012-transactional-memory.md)，实际工具/事务/跨进程证据见 [Memory 验证](../validation/t08-memory.md)。

2026-10-14 首版范围文档轮（历史）仅按用户要求调整沙箱范围，未重新开启 P4 一般规划；两张新增卡接在 T-10 后，当时下一项为 T-09。文档同步与该轮未运行范围见 [范围验证](../validation/2026-10-14-sandbox-scope.md)。

T-09 的审批/输入子范围决定见 [ADR-0014](../decisions/0014-durable-approval-input.md)，实际证据见 [交互验证](../validation/t09-interactions.md)；首批历史子范围保留；整卡最新证据在本轮验证。

T-09 外部路径的决定见 [ADR-0015](../decisions/0015-external-effects-recovery.md)，完整离线证据见 [本轮验证](../validation/t09-reconciliation-recovery.md)；T-10 硬崩溃专项单独验收。

T-10 的真实 SDK 硬终止、SQLite 提交/只读/页数耗尽故障、部分成功、取消竞争及所有权矩阵见 [专项验证](../validation/t10-faults-cancellation.md)，不覆盖沙箱/真实模型/MCP/HTTP 或旧备份维护。

# 当前进度与下一轮

更新：2026-10-14（Asia/Shanghai）。

## 当前状态

- 阶段：P1 至 P4、T-01 至 T-09 完整验收；**T-10 故障与取消矩阵本地验收完成，远程 CI 待本轮提交后核对**。新增 120 项，全库 613 项，下一项依赖 T-10 验收结果选择 T-10A。T-09 的最终代码 413fa1b / CI 38497287077 为历史证据，不能代替本轮新测试的 CI；准确结果见下文与 [T-10 验证](../validation/t10-faults-cancellation.md)。
- 基线：[P4-2026-10-13](implementation-baseline.md)，API/运行契约 v3；T-08 落地见 [ADR-0012](../decisions/0012-transactional-memory.md)，首版沙箱范围前移见 [ADR-0013](../decisions/0013-first-release-code-sandbox.md)。历史 fingerprint 不覆盖，源变化由 ADR/验证说明。
- 已实现：包 0.1.0a0、类型/schema/模型协议、内存/SQLite 事务存储、Runtime 所有权、Python 只读工具/权限/有界线程、Agent/SDK 流/取消/历史/限额、上下文选择/预览/来源清单；内置 Memory 三工具的原子执行/CAS/墓碑、作用域查询、限时检索与子预算/固定快照；保存的原工具计划、持久审批/输入、HumanInput、respond/resume、同租户授权查看/响应及不可变回执；本轮新增外部单次写入/unknown、核查、verify、分页启动扫描与 recover。
- 未实现：真实模型/MCP、HTTP/工作台、维护迁移/备份/清理；T-11 至 T-18 及 T-10A/T-10B 未开始。最小 Python 代码沙箱已纳入首版必交，尚未实现；B-14、多 Agent、Skill 与更深沙箱后续，无公共空壳。T-10 的真实 SDK 硬终止/故障/取消矩阵已本地验证，不代表 HTTP/MCP 或沙箱专项已验收。
- 无模型 Key、私有数据或付费调用；未上传 PyPI、部署官网或发布版本。
- T-08 实施起点（历史）：`/Users/yanlong/projects/drov`，main / adc9793，工作区干净且比 origin/main 多一个 T-06/07 提交；先重跑 T-07 的 343 passed / 2 skipped 后实施 T-08。保持同一 checkout，保护品牌、uv.lock、历史基线和 SQLite DDL。实施 d997f17、跨平台检查修正 40160ff/5ff654c 已提交并推送；最终补记仅修改进度/验证文档，实际 HEAD 以 git log 为准。
- 历史范围澄清（已由本轮用户要求替代）：T-08 交接时按旧 C-17 将沙箱说明为首版之后；当时仅修正状态表述。2026-10-14 用户确认首版“生成并执行代码”，现以前移后的 C-17、ADR-0013、T-10A/T-10B 为准，不能沿用旧后续排期。
- 2026-10-14 首版范围文档轮起点（历史）：同一 `/Users/yanlong/projects/drov`、main / 8f3c5e0，工作区干净且与 origin/main 同步。本轮仅修改规划、协作/开发说明与验证文档；未改 SDK 源码、测试、schema、依赖锁、DDL 或品牌，未运行/拉取容器或调用模型。文档检查点以本轮 Git 提交和下述验证记录为准。

## 已确认方向

Drov 是 Python 通用 Agent 框架，主要能力基线为 AgentScope 2.0；三个参考应用检验通用性，不限定业务。渐进 API、统一运行机制、可检查上下文/记忆是待验证的优势假设，不把类型、存储或生命周期测试写成完整 Agent 能力或体验领先。

## 本轮交付：T-10 故障与取消矩阵

起点 Local `/Users/yanlong/projects/drov`、main / 53cee41，干净且与 origin/main 同步；复跑 T-09 的 491 passed / 2 skipped 及全部静态/类型/schema 后实施。完整矩阵、命令、失败调查与适用限制见 [本轮验证](../validation/t10-faults-cancellation.md)。

| 交付 | 实际结果 |
| --- | --- |
| 真实 SDK 硬终止 | 11 个语义提交边界前后、SDK/stream 接收；独立服务写成功回包前、模型响应未提交、原两调用部分成功及 Memory 事务硬终止；新进程恢复，已发生写入不重做 |
| 提交故障 | 同 11 边界的提交前/后错误；SQLite 自身实际拒绝 COMMIT；实际 SQLITE_READONLY/SQLITE_FULL，无未提交成功事实；解除故障后显式恢复/核查 |
| 取消/观察/回执 | 两后端五组完成取消次序、部分计划取消、wait/流断开；接收事务前后取消调用方与显式 cancel；20 次重复响应/取消，原回执与成功事实保留 |
| 所有权与预算 | 非协作真实线程/Task 均有释放/硬终止路径；ShutdownIncomplete 保留槽位/SQLite 锁，另一个进程无法接管；停止后可重开；耗尽次数/活动预算不重置，未知用量不填 0 |
| 本地验证 | 新增 120，全库 613；3.11：611 passed / 2 skipped，3.14：613 passed；Ruff/格式/mypy 75 files/schema 通过；两解释器 wheel/sdist/重建、共四套干净核心安装、五示例普通/stream 通过；远程证据待补 |
| 保护范围 | 无 SDK/公共 schema/DDL/uv.lock/品牌/历史 fingerprint 变更；仅新增测试夹具与事项服务屏障，无 Key/私有数据/付费调用/发布/部署 |

本地没有剩余 T-10 任务卡缺项，远程矩阵在本轮同步后核对。模型、token/金额是脚本合成值；独立事项服务写入计数是真实本地事实，不能由此宣称真实模型质量、通用 exactly-once 或断电/设备故障安全。HTTP/MCP 生命周期由 T-12/T-13 验收；旧备份 barrier 待 T-15。

## T-09 外部核查与恢复（历史整卡验收）

起点 main / 7809192，干净且与 origin/main 同步。复跑首批 446 passed / 2 skipped、静态/类型/schema 后补完 T-09；决定见 [ADR-0015](../decisions/0015-external-effects-recovery.md)，完整证据与限制见 [本轮验证](../validation/t09-reconciliation-recovery.md)。

| 交付 | 实际结果 |
| --- | --- |
| 外部写入 | read 保留瞬态重试，write/unknown 单次派发；回包丢失/非法结果/写成功后瞬态错误保守 unknown，不盲重试 |
| 核查响应 | applied 校验原结果，not_applied 要求旧调用停止并重新审批；unresolved 原子换新请求；取消/预算耗尽只凭已核实效果收尾，无新模型/写入 |
| verify | 原用户/operation/依赖只读查询，权限/正文复核、独立期限、一个真实 Task/槽位；超时保留调用/锁，迟到建议不能消费旧请求；终态后审计不改终态 |
| recover/扫描 | 分页 104 条跨归属遗留记录，扫描不执行；原 revision/engine/input/plan/预算复核；已提交输入/结果/最终响应直接复用，未提交响应只按原输入与剩余次数重试 |
| S-03/例子 | 独立非幂等事项进程和实际计数；五组正常/拒绝/输入/丢回包/取消各 3–4 个 SDK 新进程，普通/stream 均通过，拒绝 0、其他 1 次写入；第一批参数/身份/撤权与重复回执回归保留 |
| 验证 | 新增 49 项、撤去四个过期不支持写入断言，全库 493；本地 3.11：491 passed / 2 skipped，3.14：493 passed；静态/格式/mypy 72 files/schema 通过，两解释器各两套干净核心安装、五示例普通/stream、wheel/sdist/重建/Twine 通过；413fa1b 的 10 个远程 CI job 全部 success，准确平台计数见验证记录 |
| 保护/兼容 | 两个 verification 事件增量已生成 schema；旧操作可读，无 verifier 定义不增加指纹键；无新依赖/uv.lock/DDL/品牌/历史基线修改；无 Key、付费调用、公开发布或部署 |

**T-09 任务卡的五个 S-03 离线路径及 B-01/04/05/07 功能条件已本地与远程验收，未将 T-10 的硬崩溃专项写成完成。** 模型是 ScriptedModel，计数是实际外部合成服务事实；不代表真实模型质量或外部通用 exactly-once。原调用不协作仍可能 ShutdownIncomplete；旧备份 barrier/维护仍待 T-15。

## T-09 首批审批/输入交付（历史子范围）

起点为当前 Local `/Users/yanlong/projects/drov`、main / d9bafbc，工作区干净并与 origin/main 同步。阅读 AGENTS/本页/导航/handoff、T-08 与范围验证；前置复跑 398 passed / 2 skipped 及静态/格式/类型/schema 全通过后实施。决定见 [ADR-0014](../decisions/0014-durable-approval-input.md)，实际环境/命令/子范围证据见 [T-09 验证](../validation/t09-interactions.md)。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 保存原计划 | 检查点引用已提交响应/稳定操作 ID；审批后不重问模型，已有结果不重派发，多调用顺序及累计 step/attempt/tool/活动时间保留 |
| 审批/回执 | 显式 Runtime 的 read PythonTool/Memory ASK 保存审批；批准/拒绝、20 次重复响应、冲突、到期、原参数/配置/归属绑定，响应/消费/状态/事件同事务；容量或缺 revision 不消费 |
| HumanInput | 固定注册 schema，合法答案原子成为原工具结果，非法答案可修正；可先审批再输入；临时入口仍 interaction_unavailable，无恢复 ID |
| 权限/取消 | 同 tenant 跨用户 Authorizer 分别授予 respond/read/view_content；执行始终原用户，撤权阻止派发或答案消费；取消竞争无重复派发，已成功 Memory 保留；调度失败保留回执及 recovery 暂停 |
| 实际示例 | interactions_offline 普通/stream，批准/拒绝各四个独立进程，重建原 revision、固定参数、实际只读结果和计数 1/0；重复回执不调用模型 |
| 本地回归 | 新增 48 项，全库 448 项；Python 3.11.9：446 passed / 2 skipped，Python 3.14.3：448 passed；静态/格式/严格 mypy（66 files）及 schema 通过；两解释器共四次干净安装/四示例普通与 stream、wheel/sdist 重建及 Twine 通过 |
| 保护范围 | 无新依赖、公共 DTO/schema、DDL/uv.lock/历史基线变化；品牌未改。外部 write/unknown 接收前继续拒绝，没有 verify/recover 或沙箱空壳；无 Key/付费调用/包上传/部署 |

**首批当时只验收审批与补充输入子范围，没有关闭 T-09；本轮完成范围见上表。** 首批交接时外部写入/核查/恢复未实现，现由本轮补齐；只读计数/Memory 事务仍不能替代外部证据。T-10 与沙箱卡没有随首批提前开始。

## 2026-10-14 首版代码沙箱范围调整（历史文档交付）

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 范围与依赖 | C-17 最小 Python 单次隔离执行前移为正式首版必交；保留历史编号，增加 T-10A 闭环、T-10B 隔离/故障/恢复，接在 T-10 后；T-11 至发布路径增加相应前置与门槛 |
| 工作契约 | ADR-0013、API、运行/工程/架构同步：显式 Docker/Linux、固定可信镜像、无网络/主机挂载/凭据、有限资源；代码专用工具沿现有审批，实际执行/清理租约持久化，恢复不重跑已派发代码 |
| 案例与质量 | 原三类应用各增加代码变体及 X-03/B-16/17；离线真实容器与故障矩阵、真实模型代码子组、安装/HTTP/工作台/维护/发布验收联动；试验量与阈值只在 quality-plan 维护 |
| 状态与交接 | 导航、历史基线差异、开发/例子/已实现参考同步；T-08 Memory 说明片段修正正文读取参数，未改示例源码；接续 prompt 已足够，保留从 progress 选任务并补记用户 commit/push 授权 |
| 文档验证 | 本地链接、Python 片段语法、20 张任务卡/依赖图无环及验收映射、评测算术、Git 差异与保护范围检查，见 [范围调整验证](../validation/2026-10-14-sandbox-scope.md)；只做文档检查，不把语法/历史 CI 写成新功能通过 |

**已完成的是任务与源契约调整；代码生成/沙箱执行仍未实现、未运行验证。** 不建立公共空壳，不提前关闭 T-10A/T-10B。没有选择/预装实际镜像或核实 Docker daemon、版本/资源能力，平台成绩也未产生；没有真实模型质量、费用或隔离实测。历史 P4 fingerprint 保留不覆盖，旧审查成绩不能代表新增两卡。

恢复顺序为 T-09 → T-10 → T-10A → T-10B → 后续集成/评测/发布。资源缺失时先交付不依赖资源的配置/协议子范围，并记录已验收子范围、真实后端缺项、环境准备与复跑步骤；无 daemon 的 skip/替身成功不能关闭沙箱任务或正式发布门槛。Skill 与更强/云/持久沙箱没有随本轮扩展。

## T-08 历史交付（完整验收）

| 交付 | 实际结果 |
| --- | --- |
| 事务工具 | remember/recall/forget 与结果/来源、Run 摘要、预算/检查点及 memory.changed 同事务；权限复核、CAS 冲突、重复操作、大小限额/提交失败不产生虚假成功 |
| 版本/隔离 | 新建 1、删除墓碑 2、按版本重建 3；tenant/user/Agent 查询阶段隔离，最近/精确键/NFKC+casefold 子串及稳定顺序、跨页匹配 |
| 检索与快照 | required 失败/超时模型/step 0；best_effort 迟到不用、清理有界；保存前复核/删除重建，提交后重试固定；实际消息/协议开销进入 Memory 子预算 |
| 清单/权限 | 真实版本与 opaque 来源 ID、排除原因；显式 recall 的预览来源同步标记，元数据无私有 key/content；原有正文动态授权回归保持 |
| 跨进程示例 | [memory_offline.py](../../examples/memory_offline.py) 普通/stream 各七个独立进程验证保存、跨会话读取、三个隔离组、删除及删除后读取；无网络/真实模型 |
| 文档/兼容 | ADR-0012、API/运行/工程/架构/质量/S-02/开发说明同步；旧操作可用默认空 memory_sources 读取；无新增依赖、公共 schema 或 SQLite DDL |

新增 56 项 Memory 测试，移除一个过期的 Memory 拒绝参数断言，全库 400 项。Python 3.11.9：398 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：400 passed。静态/类型/schema、两个解释器的 wheel/sdist/重建、各两个干净核心安装及三个示例的普通/stream 入口通过；详见 [T-08 验证](../validation/t08-memory.md)。

T-08 历史交付无剩余任务卡本地缺项。只保证基础 scoped 检索与本地事务，不代表真实模型记忆质量、大规模检索性能或语义检索。forget 不删除历史/输入快照；非协作后端超出清理宽限时保留任务/槽位并抛 PersistenceUnavailable，不伪造 durable 终态。T-08 当时所有 ASK 失败；本轮 T-09 已交付审批/输入子范围，外部未知效果与显式恢复仍待后续验收。

## T-07 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 完整组与来源 | 一个旧 Run 的用户输入、完整响应、工具结果和 provider_items 一起选取/排除；保护当前协议链、系统规则、schema；保存全部候选来源及实际版本/hash |
| 预算与选择 | 已知窗口扣输出预留并取显式/默认上限，未知窗口要求配置；请求 JSON/schema/provider 与协议开销全部粗估；最近组优先，装不下继续尝试更旧组，最终顺序稳定 |
| 工具预览 | 带类型、ref/version/hash/字节数/截断标记的 UTF-8 摘录；当前结果在紧预算下确定性缩小，完整引用/保护项无法装入则拒绝；原结果不改写、不重派发 |
| 原子输入/清单 | 读取候选、保存 model_input/context、事件及检查点同事务；提交失败计数 0；拒绝仍有 rejected 清单，重试保持同一输入/清单/hash |
| 查询与权限 | get_context 默认元数据、正文两次复核；provider 正文永不展示；inspect 只索引当前事件页清单，sources=[] 为摘要，详情实际可读；SQLite 重开和返回对象隔离通过 |
| 示例与契约 | [预览示例](../../examples/context_preview_offline.py) 普通/stream 入口真实工具+合成长资料，报告明确未读全文；ADR-0011/API/运行/工程/架构/质量/案例及生成 schema 同步 |

新增 [test_context.py](../../tests/test_context.py) 35 项，全库 345 项。Python 3.11.9：343 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：345 passed。Ruff、格式、严格 mypy、schema、wheel/sdist/重建、两个解释器各两个干净核心安装及检索/预览普通/stream 示例通过；详见 [T-07 验证](../validation/t07-context.md)。

T-07 任务卡无剩余本地验收缺项。估算是显式启发式，不是精确 tokenizer/费用或硬上界；选取/清单需扫描已提交会话，详细来源规模随历史增长，未承诺超长历史性能。T-06 存量输入没有清单，不回填伪造来源；新记录按保存的调用 ID 查询。只验证 macOS 本地 3.11/3.14，不把历史 CI 当本轮跨平台证据；Memory 时序的 B-08 部分仍属 T-08，真实质量仍待 T-11/T-16。正文仍仅支持 context 本人的 tenant/user，完整交互/恢复仍待 T-09。

## T-06 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 入口与接收 | start/run/wait、临时/同步入口实际执行；去重先于容量/忙；控制 Task 保留接收提交/交接所有权；不支持配置接收前拒绝 |
| 模型与工具 | 固定输入、独立 attempt、完整响应/稳定操作计划同事务；实际只读工具顺序执行、严格类型结果；ASK 明确失败且计数 0 |
| 历史与预算 | 输入事实只读一次；Session 顺序号防相同时间戳乱序；失败/取消计划有明确结果；模型/工具计数、活动预留及已知/未知用量持久保存 |
| 观察与权限 | 元数据/正文分离、便利入口预检、缓存事件/最终结果动态撤权；流退出不取消执行；有界增量/gap、durable 分页补读与当前终态追平 |
| 取消与故障 | 显式取消落盘，终态取消幂等；未停止实际模型/线程保留槽位/锁；接收/计划/派发/最终提交故障无虚假成功 |
| 示例与安装 | [离线检索](../../examples/README.md) 普通/stream 两入口；真实工具+合成资料+预设模型、Report/实际引用核对；两解释器的干净安装执行验证 |
| 源契约 | ADR-0010、API/运行/架构/工程/质量/S-01/开发说明同步；无新依赖或公共 DTO schema 变化 |

新增 53 项测试，全库 310 项。Python 3.11.9：308 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：310 passed。Ruff、格式、严格 mypy、schema、wheel/sdist/重建、干净安装后普通/流式示例通过；详见 [T-06 验证](../validation/t06-agent-loop.md)。

T-06 无剩余任务卡缺项；本地通过不代表新代码已通过远程/跨平台 CI。T-06 交付时完整历史装不下会 context_overflow、inspection.context_manifests 为空；T-07 本轮已替换为组选取/预览/实际清单。Memory/write/unknown 接收前拒绝，持久 ASK 也失败；启动扫描/显式 recover/respond/resume 仍属 T-09。当前正文读取仅 context 的 tenant/user 作用域。

## T-05 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 工具定义 | @tool 产生含真实 callable 的 PythonTool，继承 T-02 schema/codec；拒绝生成器/保留名称和注册后的对象替换 |
| 权限 | 不可变 deny/ask/allow，scope/业务否决优先；OwnerAuthorizer 与 execute_tool/ToolResource；规范参数快照、排队/派发前当前权限复核 |
| 实际只读派发 | 同步/异步函数共用校验；有界线程/队列和真实 Future；ASK 不造交互 ID，write/unknown 明确拒绝执行 |
| 操作与预算 | payload schema=1；操作/Run 摘要、检查点、事件同事务；派发预算预留持久化，重试用独立 attempt ID；重复操作不自动执行 |
| 故障 | 未授权/提交失败计数 0；输出非法/超大不成功、不重试；迟到结果不用；取消未停止调用保留槽位/锁，关闭失败可重试 |
| 文档与依赖 | ADR-0009、API/运行/架构/工程/质量/案例同步；完整 T-05 验收的 T-03/T-04 前置补入任务卡；新增公开权限 schema，无新依赖 |

新增 52 项测试，共收集 257 项；本地 Python 3.11.9/SQLite 3.45.1：256 passed、1 skipped（即时任务工厂 API）；Python 3.14.3/SQLite 3.52.0：257 passed。静态/类型/schema、构建、干净安装及故障实验见 [T-05 验证](../validation/t05-tools.md)。T-05 无剩余验收子项；完整 Agent/交互与正文授权仍按后续任务实施。T-04 历史证据见 [所有权验证](../validation/t04-runtime.md)。

## GitHub / CI

- 本轮 T-09 完整实施 d33fb5b、严格版本拒绝 ab241dc、跨平台夹具修正 fd3d7d5/[413fa1b](https://github.com/drov-ai/drov/commit/413fa1b35b9f4b7d3f967b83a46631a2ed0662d4) 已提交并推送 origin/main；最终 [CI 38497287077](https://github.com/drov-ai/drov/actions/runs/38497287077) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。八个 package job 的完整测试/构建、两套核心干净安装、五个示例普通/stream、extras 隔离导入通过；准确计数/三轮失败与修正见 [完整验证](../validation/t09-reconciliation-recovery.md)。最终证据补记只改文档，以 skip ci 保存；实际 HEAD 与同步状态以 git log/status 为准，无真实模型、包发布或部署。

- 历史 T-09 首批审批/输入实施 [9993500](https://github.com/drov-ai/drov/commit/99935000bee29a52fc93292a0dcb321657ff3187) 已提交并推送 origin/main；[CI 38436332419](https://github.com/drov-ai/drov/actions/runs/38436332419) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。八个 package job 的测试/构建/两套核心干净安装、四示例普通与 stream、extras 隔离导入通过；准确计数/范围见 [T-09 验证](../validation/t09-interactions.md)。本补记仅改 progress/验证文档，后续以 skip ci 保存，不重跑不变代码；实际 HEAD 与同步状态以 git log/status 为准。

- 首版范围文档轮检查点（历史）7cc0fc4 已提交并推送既有 origin/main，推送后工作区干净、与远程同步；本记录随后补记该事实。两次均为 `[skip ci]` 文档提交，不重跑不变的 SDK 矩阵；实际最新提交以 git log/status 为准，保护旧验证与品牌。

- 继续使用现有 origin 和用户已有 GitHub 身份；不修改全局代理/认证、不建立新仓库。提交前检查可识别凭据模式与 Git 跟踪范围，品牌/环境/数据库排除。
- 历史 T-08：实施 [d997f17](https://github.com/drov-ai/drov/commit/d997f17f7bcf34f6ceea170b378fab956240923b)，跨平台检查修正 40160ff / [5ff654c](https://github.com/drov-ai/drov/commit/5ff654cf0ac8044125554029acae0638bc85ca6a) 已推送 main（同时包含 T-06/07 的 adc9793）。最终 [CI 38413817284](https://github.com/drov-ai/drov/actions/runs/38413817284) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success；测试、构建、核心干净安装/三个离线示例、extras 隔离导入通过。两轮失败原因、适用跳过和恢复证据见 [T-08 验证](../validation/t08-memory.md)。
- 最终补记为文档提交，不更改已验证代码；不再重复运行矩阵。当前无 T-08 本地缺项或远程 CI 失败，未做真实模型评测、包发布或部署。
- T-04 实现提交 [45a57ea](https://github.com/drov-ai/drov/commit/45a57eab30b633889f625e2cde80b00c6b532205) 已推送 main；[CI 38387899628](https://github.com/drov-ai/drov/actions/runs/38387899628) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14 和 Python 3.15 advisory 共 10 个 job 全部 success。测试/构建/干净安装/extras 导入通过；3.11 即时任务 API 和 Windows 打开文件替换用例的适用跳过见 T-04 报告。
- T-05 实现 d84c66f 与最终修正 [ecc734b](https://github.com/drov-ai/drov/commit/ecc734ba595a43579172d7631153925052962208) 已推送 main；[CI 38396712936](https://github.com/drov-ai/drov/actions/runs/38396712936) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。实际测试数量/适用跳过及构建/安装/extras 证据见 T-05 验证。
- 随后补记 CI 的文档提交不改变已验证代码；工作区检查点和交接完整，无 T-05 验收缺项。
- 历史：[T-03 CI 38345105142](https://github.com/drov-ai/drov/actions/runs/38345105142) 对 751ff55 成功；平台/跳过项见 [T-03 验证](../validation/t03-storage.md)。

## 下一项：T-10A Python 代码沙箱闭环

T-10 本地矩阵已通过，本轮先同步并核对远程；远程发现失败先修正。通过后按 [任务卡](implementation-plan.md)、C-17、ADR-0013 与运行契约第 13 节实施 T-10A：

1. 核实实际 Docker/Linux 后端与预装可信 Python 镜像 digest/平台/许可，检查所需资源控制。配置/协议先完成；无可用后端也不创建空壳或宿主执行回退。
2. 落地 PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult、依赖注入与接收前预检、code-only schema、控制配置指纹；宿主配置不能来自模型或用户输入。
3. 沿原审批保存代码/hash/稳定执行键/预算后才执行；真实逐调用容器、有界 stdout/stderr/退出事实；原操作不重复执行，同进程两次调用不共享工作目录/变量。
4. 提交实际 code → 容器计算 → 工具结果 → 类型化最终输出的离线示例与完整 T-10A 证据，再进入 T-10B 隔离/资源/清理故障。无 daemon/镜像时仅记已验收配置子范围，不关闭整卡。

硬终止测试的恢复步骤已固化在本轮夹具；核查使用 verify 后 respond/resume，取消标志提交而未收尾时重复显式 cancel，普通 recovery 用 recover。没有要求重跑模型/旧写入来代替已提交结果。

## 用户准备事项

| 事项 | 当前状态 / 需要时点与配置方式 |
| --- | --- |
| T-10 故障与取消 | 本轮矩阵不需要新增仓库/账号/Key/私有资料；独立合成服务/真实 SDK 屏障、SQLite 错误与恢复已本地通过，远程同步由执行者完成 |
| GitHub 仓库 | 已提供，继续使用当前 origin；有具体同步/CI 阻碍再说明，无需重新建仓库 |
| T-10A/T-10B 本地沙箱环境与镜像 | 下一轮真实执行前需要：可用的 Docker Engine/Linux 容器（macOS 可用 Docker Desktop 的 Linux VM）、预装可信 Python 镜像的具体 `@sha256:…`/linux 平台/许可；内存、CPU、PID、tmpfs 等控制须实际核实。当前仅历史确认 CLI 路径，未验证 daemon/版本/镜像。控制配置保留宿主，不给容器挂控制 socket、仓库或凭据，不自动拉镜像；无需新增云账号、模型 Key 或付费预算 |
| 模型服务、可访问 model ID、密钥和总预算 | T-11 smoke/T-16 live 前提供可访问服务/model ID 并批准共同费用上限；工程执行者先按新增代码任务量估价。Key 仅本地环境/忽略配置或凭据管理，聊天/Git 不贴密钥；未授权预算前不付费调用 |
| PyPI/TestPyPI 名称/权限、发布身份 | T-17/T-18 有具体构件时核实；到时按发布策略提供账号权限/安全发布身份，不假定包名可用 |
| Cloudflare drop.dev、账号范围、品牌来源与公开使用范围 | 官网/素材初次公开前核实；现有素材保留本地，当前不部署 |

完整发布条件仍在 [release-policy](release-policy.md)，不另建第二套范围。

## 会话切换建议

**下一轮建议 New Chat，实施 T-10A。** 本轮先完成 T-10 的远程核对和 Git 证据；沙箱引入实际容器后端、隔离与清理语义，适合独立会话。继续选择 Local `/Users/yanlong/projects/drov`，读 AGENTS/progress/导航/handoff、本轮验证及 ADR-0013、运行第 13 节/工程方案，再核实 Git 和实际容器条件。无需新仓库/worktree或模型 Key；环境缺失时先完成独立配置子范围，保留真实后端缺项。

## 阶段记录

| 日期 | 阶段 | 结果 |
| --- | --- | --- |
| 2026-10-12 | P1–P3 | 产品/API、架构/运行、工程/任务/质量/维护规划完成 |
| 2026-10-13 | P4 | v3 契约、ADR-0005、历史基线与代码就绪审查通过 |
| 2026-10-13 | T-01 | 工程骨架、锁、构建/导入/CI 配置完成 |
| 2026-10-13 | T-02 | 公共类型/schema/适配协议、116 项离线验证完成 |
| 2026-10-13 | T-03 | 内存/SQLite、CAS/去重/原子边界、锁/取消/真实进程验证完成 |
| 2026-10-13 | T-04 | 注册指纹/恢复校验接点、资源/任务/真实调用所有权、取消/容量/关闭完整验收；下一项 T-05 |
| 2026-10-13 | T-05 | Python 工具/权限、真实只读派发、有界队列/记录/重试与故障验收完成；下一项 T-06 |
| 2026-10-13 | T-06 | 只读 Agent/固定模型计划、历史/限额、正文授权、SDK 流/取消、离线检索与安装执行本地验收完成；下一项 T-07；未推送新 CI |
| 2026-10-13 | T-07 | 完整上下文组选取、确定性预算/预览、固定来源清单、授权查询及安装执行本地验收完成；下一项 T-08；未推送新 CI |
| 2026-10-13 | T-08 | 内置 Memory 原子工具/CAS/墓碑、隔离/限时检索/子预算/固定快照、七进程普通/stream 示例及 400 项回归本地验收完成；10 个远程 CI job success；下一项 T-09 |
| 2026-10-14 | 首版范围调整 | 用户确认代码生成/执行首版必交，新增 T-10A/T-10B，契约/案例/质量/发布及交接文档同步并检查；沙箱未实施，下一项仍 T-09 |
| 2026-10-14 | T-09 子范围 | 审批/输入、原子 respond/resume、原计划/预算与回执、同 tenant 授权及四进程示例本地验收；外部未知效果/verify/recover 未实施，整卡进行中 |
| 2026-10-14 | T-09 整卡验收 | 外部单次写入/unknown、verify/核查响应、启动扫描/显式步骤恢复、五组独立服务/多进程 SDK 与 stream 示例及 493 项回归；413fa1b 的 10 个远程 CI job success；T-10 硬崩溃专项尚未开始 |
| 2026-10-14 | T-10 本地验收 | 120 项硬终止/真实 SQLite 故障/取消竞争新增；全库 613、两解释器/构建/安装通过；本轮远程待核对，下一项 T-10A 尚未开始 |

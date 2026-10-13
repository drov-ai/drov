# 当前进度与下一轮

更新：2026-10-13（Asia/Shanghai）。

## 当前状态

- 阶段：P1 至 P4、T-01 至 T-08 **本地实施验收完成**；下一项 T-09 审批与显式恢复。本轮远程同步/CI 结果结束前补记。
- 基线：[P4-2026-10-13](implementation-baseline.md)，API/运行契约 v3；本轮细化见 [ADR-0012](../decisions/0012-transactional-memory.md)。历史 fingerprint 不覆盖，源变化由 ADR/验证说明。
- 已实现：包 0.1.0a0、类型/schema/模型协议、内存/SQLite 事务存储、Runtime 所有权、Python 只读工具/权限/有界线程、Agent/SDK 流/取消/历史/限额、上下文选择/预览/来源清单；新增内置 Memory 三工具的原子执行/CAS/墓碑、作用域查询、限时检索与子预算/固定快照。
- 未实现：完整审批/核查/HumanInput/显式恢复、真实模型/MCP、HTTP/工作台、维护迁移/备份/清理；T-09 至 T-18 未开始。B-14、多 Agent、Skill/沙箱等后续能力无公共空壳。
- 无模型 Key、私有数据或付费调用；未上传 PyPI、部署官网或发布版本。
- 本轮起点：`/Users/yanlong/projects/drov`，main / adc9793，工作区干净且比 origin/main 多一个 T-06/07 提交；先重跑 T-07 的 343 passed / 2 skipped 后实施 T-08。保持同一 checkout，保护品牌、uv.lock、历史基线和 SQLite DDL。

## 已确认方向

Drov 是 Python 通用 Agent 框架，主要能力基线为 AgentScope 2.0；三个参考应用检验通用性，不限定业务。渐进 API、统一运行机制、可检查上下文/记忆是待验证的优势假设，不把类型、存储或生命周期测试写成完整 Agent 能力或体验领先。

## T-08 本轮交付

| 交付 | 实际结果 |
| --- | --- |
| 事务工具 | remember/recall/forget 与结果/来源、Run 摘要、预算/检查点及 memory.changed 同事务；权限复核、CAS 冲突、重复操作、大小限额/提交失败不产生虚假成功 |
| 版本/隔离 | 新建 1、删除墓碑 2、按版本重建 3；tenant/user/Agent 查询阶段隔离，最近/精确键/NFKC+casefold 子串及稳定顺序、跨页匹配 |
| 检索与快照 | required 失败/超时模型/step 0；best_effort 迟到不用、清理有界；保存前复核/删除重建，提交后重试固定；实际消息/协议开销进入 Memory 子预算 |
| 清单/权限 | 真实版本与 opaque 来源 ID、排除原因；显式 recall 的预览来源同步标记，元数据无私有 key/content；原有正文动态授权回归保持 |
| 跨进程示例 | [memory_offline.py](../../examples/memory_offline.py) 普通/stream 各七个独立进程验证保存、跨会话读取、三个隔离组、删除及删除后读取；无网络/真实模型 |
| 文档/兼容 | ADR-0012、API/运行/工程/架构/质量/S-02/开发说明同步；旧操作可用默认空 memory_sources 读取；无新增依赖、公共 schema 或 SQLite DDL |

新增 56 项 Memory 测试，移除一个过期的 Memory 拒绝参数断言，全库 400 项。Python 3.11.9：398 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：400 passed。静态/类型/schema、两个解释器的 wheel/sdist/重建、各两个干净核心安装及三个示例的普通/stream 入口通过；详见 [T-08 验证](../validation/t08-memory.md)。

T-08 无剩余任务卡本地缺项。只保证基础 scoped 检索与本地事务，不代表真实模型记忆质量、大规模检索性能或语义检索。forget 不删除历史/输入快照；非协作后端超出清理宽限时保留任务/槽位并抛 PersistenceUnavailable，不伪造 durable 终态。所有 ASK 仍失败；完整交互/显式恢复和外部写入未知效果待 T-09/T-10。

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

- 继续使用现有 origin 和用户已有 GitHub 身份；不修改全局代理/认证、不建立新仓库。提交前检查可识别凭据模式与 Git 跟踪范围，品牌/环境/数据库排除。
- T-04 实现提交 [45a57ea](https://github.com/drov-ai/drov/commit/45a57eab30b633889f625e2cde80b00c6b532205) 已推送 main；[CI 38387899628](https://github.com/drov-ai/drov/actions/runs/38387899628) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14 和 Python 3.15 advisory 共 10 个 job 全部 success。测试/构建/干净安装/extras 导入通过；3.11 即时任务 API 和 Windows 打开文件替换用例的适用跳过见 T-04 报告。
- T-05 实现 d84c66f 与最终修正 [ecc734b](https://github.com/drov-ai/drov/commit/ecc734ba595a43579172d7631153925052962208) 已推送 main；[CI 38396712936](https://github.com/drov-ai/drov/actions/runs/38396712936) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。实际测试数量/适用跳过及构建/安装/extras 证据见 T-05 验证。
- 随后补记 CI 的文档提交不改变已验证代码；工作区检查点和交接完整，无 T-05 验收缺项。
- 历史：[T-03 CI 38345105142](https://github.com/drov-ai/drov/actions/runs/38345105142) 对 751ff55 成功；平台/跳过项见 [T-03 验证](../validation/t03-storage.md)。

## 下一项：T-09 审批与显式恢复

前置 T-08 已完整本地验收；按 [实施计划](implementation-plan.md) 的 T-09 卡推进，不再追加一般规划轮。

1. 持久审批请求、到期/重复响应、原参数与归属绑定、原子响应/推进；派发前动态权限复核，取消不重派发。
2. HumanInput 以保存的原调用及 schema 消费回答，不重跑已提交工具，不开放任意函数栈恢复。
3. 外部写入 unknown/reconciliation、verify、respond/resume/recover：保持原 revision/指纹、预算、操作结果和所有权；合法控制不绕过审批/核查。
4. 完成 S-03-A/B/C/D/E 全部离线路径，审批 example；先验收审批响应子范围，再 HumanInput，再核查/恢复。T-10 再承担完整真实进程崩溃/故障矩阵。

完成标准：整张 T-09 卡要求的五个 S-03 离线路径及 B-01/04/05/07 有证据；无额外写入、无旧审批复用或参数/身份伪造、合法重复响应不重派发，取消未知操作仍待核查。分批完成时记录子范围，不提前关闭 T-09。

## 用户准备事项

| 事项 | 当前状态 / 需要时点与配置方式 |
| --- | --- |
| T-09 审批与显式恢复 | 现在无需新增仓库/账号/Key/私有资料；复用当前仓库、SQLite、ScriptedModel 和自有合成审批/输入/核查夹具，不接真实业务写服务 |
| GitHub 仓库 | 已提供，继续使用当前 origin；有具体同步/CI 阻碍再说明，无需重新建仓库 |
| 模型服务、可访问 model ID、密钥和总预算 | T-11 smoke/T-16 live 前主动提出服务配置与费用估算；到时在本地环境或忽略配置中安全设置 Key，聊天不贴密钥；未授权预算前不付费调用 |
| PyPI/TestPyPI 名称/权限、发布身份 | T-17/T-18 有具体构件时核实；到时按发布策略提供账号权限/安全发布身份，不假定包名可用 |
| Cloudflare drop.dev、账号范围、品牌来源与公开使用范围 | 官网/素材初次公开前核实；现有素材保留本地，当前不部署 |

完整发布条件仍在 [release-policy](release-policy.md)，不另建第二套范围。

## 会话切换建议

**下一轮建议 New Chat，实施 T-09。** T-08 已完整验收，审批/核查/恢复进入新的复杂模块边界；新会话按项目文件接续，避免把本轮 Memory 的专用原子写路径误用到外部写入。

选择 Drov 项目 **Local / 当前目录 `/Users/yanlong/projects/drov`**。阅读 AGENTS/progress/导航/handoff 及 T-08 验证，核实最新 Git/远程状态；运行锁定静态/schema/pytest 检查，再按 T-09 卡分批实施，保持 T-08 Memory 回归。无需为了新会话新建仓库或 worktree，也不要切回缺少 T-06/07/08 的旧 checkout。

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

| 2026-10-13 | T-08 | 内置 Memory 原子工具/CAS/墓碑、隔离/限时检索/子预算/固定快照、七进程普通/stream 示例及 400 项回归本地验收完成；下一项 T-09 |

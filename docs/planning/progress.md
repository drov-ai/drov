# 当前进度与下一轮

更新：2026-10-13（Asia/Shanghai）。

## 当前状态

- 阶段：P1 至 P4、T-01 至 T-07 **本地实施验收完成**；下一项 T-08 内置 Memory。T-05 远程 CI 为历史证据，本轮未推送/发布，未运行新远程 CI。
- 基线：[P4-2026-10-13](implementation-baseline.md)，API/运行契约 v3；T-06/07 细化见 [ADR-0010](../decisions/0010-read-only-agent-loop.md) / [ADR-0011](../decisions/0011-context-assembly.md)。历史 fingerprint JSON 不覆盖，源变化由 ADR/验证说明。
- 已实现：包 0.1.0a0、定义/类型/配置/schema/模型协议、内存/SQLite 事务存储、Runtime 注册/资源/任务所有权、PythonTool/权限/有界线程、只读 Agent/SDK 流/取消、历史与限额；新增完整轮次上下文选择、结果预览、输入/来源清单原子保存、get_context 和有界检查索引。
- 未实现：Memory 检索/工具、完整审批/核查/显式恢复、真实模型/MCP、HTTP/工作台、维护迁移/备份/清理；T-08 至 T-18 未开始。B-14、多 Agent、Skill/沙箱等后续能力无公共空壳。
- 无模型 Key、私有数据或付费调用；未上传 PyPI、部署官网或发布版本。
- 本轮起点：`/Users/yanlong/projects/drov`，main / 0b2ac9b，含上一轮 T-06 的未提交源码/文档/示例；先重跑 308 passed / 2 skipped 核实前置，再实施 T-07。本轮在原 checkout 保留 T-06/07 并保存本地提交检查点（具体提交以 git log 为准），不推送；下轮继续这个目录。已有 origin、品牌、uv.lock、历史基线和 SQLite DDL 不变。

## 已确认方向

Drov 是 Python 通用 Agent 框架，主要能力基线为 AgentScope 2.0；三个参考应用检验通用性，不限定业务。渐进 API、统一运行机制、可检查上下文/记忆是待验证的优势假设，不把类型、存储或生命周期测试写成完整 Agent 能力或体验领先。

## T-07 本轮交付

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

## 下一项：T-08 内置 Memory

前置 T-07 已完整本地验收；按 [实施计划](implementation-plan.md) 的 T-08 卡接入实际 Memory，不追加无具体缺项的一般规划轮。

1. remember/recall/forget 连接内置事务存储，工具成功结果与写入/版本墓碑同事务，落实租户/用户/Agent 隔离。
2. required/best_effort 检索落实期限、迟到清理、最近/键/子串规则及 ContextPolicy 子预算；清单记录真实来源版本、检索状态和排除原因。
3. 完成并发 CAS、删除/快照边界、required 超时模型计数 0、best_effort 迟到不用及重试快照不变测试。
4. 跑通跨进程保存后新会话读取的 S-02 离线 example，以及跨用户/租户/Agent 隔离组；先解决完整 Memory 的写工具权限/事务派发接点，不能只解除接收拒绝。

完成标准为 T-08 的跨进程、事务、删除、隔离和检索时序全部有对应证据；不以存储底层已有 MemoryRecord 或脚本模型声称记住作为完成。T-09 审批/显式恢复随后按依赖推进。

## 用户准备事项

| 事项 | 当前状态 / 需要时点与配置方式 |
| --- | --- |
| T-08 内置 Memory | 现在无需新增账号/Key/私有资料；复用当前仓库、SQLite、ScriptedModel 和自有合成记忆夹具，跨进程例子在本地临时目录运行 |
| GitHub 仓库 | 已提供，继续使用当前 origin；有具体同步/CI 阻碍再说明，无需重新建仓库 |
| 模型服务、可访问 model ID、密钥和总预算 | T-11 smoke/T-16 live 前主动提出服务配置与费用估算；到时在本地环境或忽略配置中安全设置 Key，聊天不贴密钥；未授权预算前不付费调用 |
| PyPI/TestPyPI 名称/权限、发布身份 | T-17/T-18 有具体构件时核实；到时按发布策略提供账号权限/安全发布身份，不假定包名可用 |
| Cloudflare drop.dev、账号范围、品牌来源与公开使用范围 | 官网/素材初次公开前核实；现有素材保留本地，当前不部署 |

完整发布条件仍在 [release-policy](release-policy.md)，不另建第二套范围。

## 会话切换建议

**下一轮继续本会话实施 T-08，无需 New Chat。** 本轮完成 T-07，上下文/事务接点与 Memory 检索依赖紧邻，继续有利于保持契约一致。T-08 完整验收后，T-09 的审批/恢复复杂度较高，届时建议 New Chat；不为换会话提前关闭任务。

若用户选择新会话，仍选择 Drov 项目 **Local / 当前目录 `/Users/yanlong/projects/drov`**，按 [handoff](../handoff.md) 核实实际 Git、progress 和 T-07 验证；不要切到只含 0b2ac9b 的旧 checkout。恢复步骤：运行锁定静态/schema/pytest 检查，按 T-08 卡接入 Memory 的原子工具与检索，保持本轮清单/预览回归通过。

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

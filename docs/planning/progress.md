# 当前进度与下一轮

更新：2026-10-13（Asia/Shanghai）。

## 当前状态

- 阶段：P1 至 P4、T-01 至 T-05 本地实施验收完成；下一项 T-06 离线运行闭环。本轮 Git/CI 状态见下节。
- 基线：[P4-2026-10-13](implementation-baseline.md)，API/运行契约 v3；T-05 细化见 [ADR-0009](../decisions/0009-python-tools.md)。历史 fingerprint JSON 不覆盖，源契约变化由 ADR 说明。
- 已实现：包 0.1.0a0、定义/公共类型/配置/工具 schema/模型协议、内存/SQLite 事务存储及记录/CAS/去重/原子步骤/Memory 原语/锁/取消处理；Runtime 注册指纹/资源与任务所有权；PythonTool/@tool、PermissionPolicy/OwnerAuthorizer、私有只读工具派发、有界线程队列及事务操作记录。
- 未实现：Agent 执行入口、Memory 工具、完整审批/核查与恢复调度、真实模型/MCP、HTTP/工作台、维护迁移/备份/清理；T-06 至 T-18 未开始。私有任务和实际线程测试不是 Agent 副作用恢复或模型质量验证。
- 无模型 Key、私有数据或付费调用；未上传 PyPI、部署官网或发布版本。
- 本轮起点：`/Users/yanlong/projects/drov`，main / e8588a7，工作区干净。origin 为已有公开仓库 [drov-ai/drov](https://github.com/drov-ai/drov) 的 SSH URL；T-01/T-02 检查点 478cb12、T-03 实现 751ff55、T-04 实现 45a57ea 已在 main。品牌源文件保留本地，不进入 Git。

## 已确认方向

Drov 是 Python 通用 Agent 框架，主要能力基线为 AgentScope 2.0；三个参考应用检验通用性，不限定业务。渐进 API、统一运行机制、可检查上下文/记忆是待验证的优势假设，不把类型、存储或生命周期测试写成完整 Agent 能力或体验领先。

## T-05 本轮交付

| 交付 | 实际结果 |
| --- | --- |
| 工具定义 | @tool 产生含真实 callable 的 PythonTool，继承 T-02 schema/codec；拒绝生成器/保留名称和注册后的对象替换 |
| 权限 | 不可变 deny/ask/allow，scope/业务否决优先；OwnerAuthorizer 与 execute_tool/ToolResource；规范参数快照、排队/派发前当前权限复核 |
| 实际只读派发 | 同步/异步函数共用校验；有界线程/队列和真实 Future；ASK 不造交互 ID，write/unknown 明确拒绝执行 |
| 操作与预算 | payload schema=1；操作/Run 摘要、检查点、事件同事务；派发预算预留持久化，重试用独立 attempt ID；重复操作不自动执行 |
| 故障 | 未授权/提交失败计数 0；输出非法/超大不成功、不重试；迟到结果不用；取消未停止调用保留槽位/锁，关闭失败可重试 |
| 文档与依赖 | ADR-0009、API/运行/架构/工程/质量/案例同步；完整 T-05 验收的 T-03/T-04 前置补入任务卡；新增公开权限 schema，无新依赖 |

新增 51 项测试，共收集 256 项；本地 Python 3.11.9/SQLite 3.45.1：255 passed、1 skipped（即时任务工厂 API）；Python 3.14.3/SQLite 3.52.0：256 passed。静态/类型/schema、构建、干净安装及故障实验见 [T-05 验证](../validation/t05-tools.md)。T-05 无剩余验收子项；完整 Agent/交互与正文授权仍按后续任务实施。T-04 历史证据见 [所有权验证](../validation/t04-runtime.md)。

## GitHub / CI

- 继续使用现有 origin 和用户已有 GitHub 身份；不修改全局代理/认证、不建立新仓库。提交前检查可识别凭据模式与 Git 跟踪范围，品牌/环境/数据库排除。
- T-04 实现提交 [45a57ea](https://github.com/drov-ai/drov/commit/45a57eab30b633889f625e2cde80b00c6b532205) 已推送 main；[CI 38387899628](https://github.com/drov-ai/drov/actions/runs/38387899628) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14 和 Python 3.15 advisory 共 10 个 job 全部 success。测试/构建/干净安装/extras 导入通过；3.11 即时任务 API 和 Windows 打开文件替换用例的适用跳过见 T-04 报告。
- T-05 本地验收已完成；本轮实现检查点与新 CI 同步后补记。上面的 T-04 CI 是历史证据，不用于声明 T-05 代码已通过远程验证。
- 历史：[T-03 CI 38345105142](https://github.com/drov-ai/drov/actions/runs/38345105142) 对 751ff55 成功；平台/跳过项见 [T-03 验证](../validation/t03-storage.md)。

## 下一轮：T-06 离线运行闭环

前置 T-04/T-05 已完整验收；按 [实施计划](implementation-plan.md) 的 T-06 卡接入 ScriptedModel，形成真实只读 Agent 闭环。

1. 实现 Runtime.start/run/wait 与 Agent.run/run_sync，接收前拒绝无 callable、未支持的 write/unknown/Memory 配置；去重先于忙/容量，accepted 提交与任务/槽位交接保持所有权。
2. 提交完整模型响应/计划后调用已实现的只读派发器，最终输出经 codec；提交历史只读一次用户输入，限额/取消的实际事实不因重启或错误重置。
3. 实现 stream/events/inspect 和正文授权投影；等待/流退出不取消执行，当前权限决定正文，持久事件与结果一致，暂停/终态订阅追平。
4. 用脚本模型、计数工具/存储故障/权限与取消夹具跑通 S-01-A，交付离线检索 example。临时 ASK 明确 failed/interaction_unavailable、工具计数 0；现有事件循环中 run_sync 明确失败，不嵌套循环。

完成标准是一个可实际运行的只读 SDK/stream 示例及对应错误/隔离测试，不是空 run 方法或示意输出。Memory/HITL/未知写入仍依赖 T-08/T-09/T-10。

## 用户准备事项

| 事项 | 当前状态 / 需要时点与配置方式 |
| --- | --- |
| T-06 离线闭环 | 现在无需准备 Key、私有数据或账号；用脚本模型、自有合成语料、工具/身份/计数夹具验收 |
| GitHub 仓库 | 已提供，继续使用当前 origin；有具体同步/CI 阻碍再说明，无需重新建仓库 |
| 模型服务、可访问 model ID、密钥和总预算 | T-11 smoke/T-16 live 前主动提出服务配置与费用估算；到时在本地环境或忽略配置中安全设置 Key，聊天不贴密钥；未授权预算前不付费调用 |
| PyPI/TestPyPI 名称/权限、发布身份 | T-17/T-18 有具体构件时核实；到时按发布策略提供账号权限/安全发布身份，不假定包名可用 |
| Cloudflare drop.dev、账号范围、品牌来源与公开使用范围 | 官网/素材初次公开前核实；现有素材保留本地，当前不部署 |

完整发布条件仍在 [release-policy](release-policy.md)，不另建第二套范围。

## 会话切换建议

**建议下一轮为 T-06 开启 New Chat。** 本会话已完整完成 T-04/T-05，两项相关内核任务留下明确检查点；T-06 将横跨接收、模型循环、历史、授权与事件流，适合独立上下文。新会话仍选择 Drov 项目 **Local / 当前目录 `/Users/yanlong/projects/drov`**，按 [handoff](../handoff.md) 读取 Git、进度和最新验证，不复制或重新建目录。

默认约 3 轮、通常 2–3 个相关任务后交接是提醒，不是硬限制；本次按完整任务边界和下一项复杂度提前交接。若用户选择继续本会话，也可以按相同证据接续；不为换会话提前关闭任务。

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

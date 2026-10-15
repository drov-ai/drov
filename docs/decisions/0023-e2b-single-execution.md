# ADR-0023：可选 E2B 单次执行与独立费用边界

日期：2026-10-15（Asia/Shanghai）。状态：工作决定，实际验收状态以 [progress](../planning/progress.md) 和 [T-10C 验证](../validation/t10c-e2b.md) 为准。补充 ADR-0016/0020/0021，不改变 Docker 必交范围。

## 原因与决定

用户明确要求先补 T-10C，授权使用已有 E2B 项目、宿主凭据和赠金额度测试。T-10B/T-11 前置已完整验收；不重开规划、不调用模型、不发布或部署。

1. 独立 `e2b==2.56.0` extra，提供 `drov.integrations.e2b.E2BSandbox/E2BLimits`。构造、核心导入和普通安装无 SDK/凭据/网络副作用；open 只读固定 build 状态，缺 extra、可变 tag、缺上传许可/报价/预算或要求区域证明时前置拒绝。宿主 resolver 按非秘密 credential_ref 解析，不存真实 Key，不把宿主环境复制给代码。
2. 私有 SandboxBackend 统一现有 Runtime 的装配、审批、运行绑定、字节暂存、原结果共同发布、授权读取和恢复；Docker 的 config hash、runner、DDL 和原数据语义保持。E2B 使用独立 VM/工作负载 profile，不伪装 Docker seccomp/cgroup 或只读根盘。数值唯一维护在 [工程第10节](../planning/engineering.md#10-可选-e2b-后端t-10c)。
3. 每个已提交 intent 只 create 一次；请求固定 `<template_id>:<build_uuid>`，显式禁外连、公共访问需宿主 token、到期 kill、禁 auto-resume，不提供 pause/Notebook/Shell 工具。实际 info 检查模板、metadata、CPU/内存、网络、生命周期/期限、无卷；guest 核对工作盘和预装分析库。项目区域只作声明，当前 SDK 不提供可强制或可证明的区域字段，要求区域证明即拒绝。
4. 可信 root monitor 与 UID 65533 代码隔离；输入及祖先归另一 UID 只读，控制目录 root 私有；代码无补充组、无 capability、no-new-privileges、内核 rlimit 和有界 tmpfs/管道。结束/超限后清除整棵 workload UID 进程树，导出复用安全 dirfd/普通文件/hash/PNG 检查。根盘可写性与 VM 规格属于 E2B profile 的差异；不声称整个宿主应用被隔离。
5. 费用采用整数 micro-USD 的保守累计预留：同一 SQLite/budget_id 汇总已提交 create intent 的完整 provider 寿命上界，不退回“已销毁”的预留；新派发与 intent 同事务准入。报价未知/预算不足拒绝新 create，原清理继续。模板构建和跨数据库验收另设宿主总账；不是账号余额/账单 API，不把估算写成实际扣费。
6. 租约增加远端资源 ID/start intent，不占用 Docker ID。E2B 显式装配将 sandbox_schema 标记为 **2**，表 DDL 不变，保留并读取 v1 Docker 数据；旧 SDK 在 open 拒绝 v2。未选 E2B 的 Docker 仍使用 v1，序列化不增加远端字段，不自动升级普通库或降级 v2。新字段不可倒退/更换；原配置指纹匹配后才能查证和清理。
7. 创建丢应答通过完整 execution/owner/config metadata 分页查找原资源并持久化发现的 ID；不把 metadata 当创建幂等。已知 ID 的 404 可以确认已不存在；丢创建应答且查询为空仍 unknown，继续保留租约、名额与关闭锁。恢复只查原资源/销毁/原观察与可靠字节；没有原结果就 failed_known，不重算，不人工伪造资源事实。

## 替代方案与代价

- 只封装 Code Interpreter/Jupyter：会引入持久上下文及额外公开服务，不适合当前单次契约；采用通用 SDK 的受控私有命令通道。
- 沿用 Docker 字段/沙箱版本：会误认资源身份且不能让旧 SDK 及时拒绝；采用专用远端事实与显式 v2 marker。
- 按观察运行秒数退费预留：丢应答/服务端延迟和账单未知时可能透支；选择保守寿命预留，代价是提前耗尽本地预算，需要新预算授权或核对后调整。
- 把项目截图当区域/余额证明：没有 SDK 实测支撑；仅记录声明和可核对的资源台账，强区域约束拒绝。

## 影响与来源

同轮更新 API、架构、运行/工程、能力/案例/质量/维护、schema、安装/CI、第三方清单和 progress。P4 指纹保留历史，不重写；core records DDL/user_version、Artifact DDL 与品牌不变。E2B 仍为首期系列可选实验性后端，SDK/stream 之外的 HTTP/UI、模型生成代码质量分别由后续任务验收。

查阅日期：2026-10-15。实际 SDK 2.56.0 wheel/源文件与 uv.lock 已核对；官方滚动文档不是固定版本承诺：[固定 build](https://docs.e2b.dev/template/tags)、[构建](https://docs.e2b.dev/template/build)、[外连控制](https://docs.e2b.dev/network/internet-access)、[公共访问](https://docs.e2b.dev/network/restrict-public-access)、[生命周期](https://docs.e2b.dev/sandbox)、[价格](https://e2b.dev/pricing)。没有复制 E2B 源码，复用与许可见 [THIRD_PARTY](../../THIRD_PARTY.md)。

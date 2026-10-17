# 实际支持范围与限制

本表描述当前开发快照的受测边界，不替代[能力表](../planning/capability-matrix.md)中的产品范围或 [progress](../planning/progress.md)中的任务状态。版本 `0.1.0a0` 尚未公开发布。

| 路径 | 已有证据 | 限制 |
| --- | --- | --- |
| 核心 SDK / SQLite / HTTP / MCP | Linux Python 3.11–3.14；macOS/Windows 3.11/3.14 的适用 CI 与安装 | 3.15 仅探测；平台跳过不是对应能力通过 |
| LocalArtifacts | POSIX 私有目录与配对/授权/损坏检查 | Windows 字节层未支持；纯 SQLite 仍可使用 |
| Docker 单次 Python | POSIX Unix socket 控制，Linux ARM64/AMD64 固定镜像；原生 Linux 3.11–3.14 专项 | 无 Windows/npipe、远程 TCP、非 Linux 后端；不自动拉镜像 |
| 分析镜像 | CPython 3.11，平台锁定 numpy/pandas/matplotlib 与许可 inventory | 不提供任意 pip/network、GPU、持久 Python 状态 |
| 可选 E2B | 固定 SDK/template/build/profile 的真实远端契约、文件、故障/清理及独立安装 | 有费用与上传边界，区域声明不等于驻留保证；本轮未调用云 |
| OpenAI Responses | 官方客户端本地 HTTP/SSE 的离线协议验证 | OpenAI 供应商真实模型质量未验证 |
| DeepSeek Responses | 固定合成任务 smoke7/7、正式78/81、schema73/75、代码9/9 | 三个失败保留；rolling alias/提示/预算绑定；无任意任务成功率承诺 |
| MCP | stdio 实际发现、固定工具、审批/调用/进程收尾 | 不含远程 MCP/OAuth/热更新发现 |
| 本地工作台 | 三场景/文件/重连与授权浏览器流程，wheel 自带资产 | 实验性 UI；不是完整审计导出或云后端选择器 |
| 维护/备份 | 合成库故障、历史 v1 字段、授权导出和恢复屏障 | 无通用屏障解除；数据库不是加密存储 |

## 威胁与数据边界

- 默认本人授权查询；应用可提供额外 Authorizer。已经传给供应商或下载到本机的数据不能从本地撤回。
- Docker 依赖可信 daemon/宿主/内核和固定 profile，不承诺敌对公网多租户强隔离。
- Python 工具和 stdio MCP 在宿主权限下运行，只有显式 PythonSandbox 使用隔离后端。
- 外部 write/unknown 不承诺 exactly-once；回包丢失需核查，不能自动重复非幂等写入。
- 本地记录保留至显式维护；forget 不删除历史，容器销毁不删除 ready 产物。
- token 粗估、云寿命预留与历史费用上界都不是供应商实际扣款/余额。

多 Agent、Skill、持久 Notebook/共享工作区、更深沙箱、调度/业务渠道集成仍为后续，没有可用公共空壳。渐进 API、入口一致和上下文可检查是设计主张；体验仅维护者验证，没有独立使用者或竞品领先结论。

证据入口：[最终平台验证](../validation/t16-evidence-scope.md)、[Docker 专项](../validation/t10b-sandbox-faults.md)、[可选 E2B](../validation/t10c-e2b.md)、[使用体验](../validation/t16-experience.md)。

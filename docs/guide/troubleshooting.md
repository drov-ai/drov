# 排错

先保存 Run ID、结构化错误码、SDK/Python 版本、所选 extra 和非敏感复现步骤。不要公开输入、完整日志、原始数据库、模型 Key、UI 令牌、云控制 ID 或业务附件。安全问题使用[私密报告](../../SECURITY.md)。

| 现象 | 含义与下一步 |
| --- | --- |
| uv 版本不匹配 | 仓库固定 0.13.0，按开发说明使用项目隔离工具环境 |
| 缺少 openai/mcp/server/e2b 模块 | 同时选择所需 extra 后锁定同步；核心导入不要求这些包 |
| `StorageInUse` | 原 Runtime/维护/未收尾调用仍持锁；确认真实所有者，不删除锁文件绕过 |
| schema/extension 不兼容 | SDK 不认识库格式；保留原数据，使用匹配版本，勿手工改 DDL/版本号 |
| `interaction_unavailable` | 临时 Agent.run 不能等待人工；改用显式 Runtime |
| 返回 suspended | 查看 pending 和原参数，用原 request_id 响应审批/输入/核查，未必是错误 |
| HTTP 404 / 看不到正文 | 先核对可信身份、scope 和 read/view_content；默认隐藏正文，不能从 404 判断别人的资源是否存在 |
| `invalid_output` | 模型输出未通过类型/schema；保留失败，不把自然语言成功自述当成合法结果 |
| `context_overflow` | 受保护内容或预算装不下；检查清单/预览/模型窗口，不能只静默截断协议链 |
| 超过限额或费用准入拒绝 | 核对持久计数、预留与管理员上限；重试不重置原 Run 预算 |
| 模型/外部工具超时 | 可能已经派发；检查操作状态，未知写入先核查，不换请求键重做 |
| `ShutdownIncomplete` | 实际调用/线程/后端尚未结束，保持资源归属，重试有界关闭并核实后端 |
| Docker 预检失败 | 检查显式 CLI/socket、固定同架构 image、Linux/cgroup v2 和安全 profile；不自动降级 |
| Artifact 无法读取或容量不足 | 检查 ready/授权/hash/配对目录/原预留；不手工删 stage.json 或字节“腾空间” |
| 回收 pending 阻止打开 | 离线用 `resume_reclamation` 继续原库存，勿删除台账伪造完成 |
| 恢复副本拒绝 start | recovery barrier 正常保护旧备份；没有通用 force 解锁，先完成独立事实核查 |

Run、操作、上下文分别从 `inspect`、`get_context` 与持久事件查起；临时 delta 缺失不说明工具没有执行。所有正文查询需要当前授权。详情见[API](api.md)和[运行原理](concepts.md)。

普通缺陷通过[GitHub issue](https://github.com/drov-ai/drov/issues/new/choose)提供最小合成复现；提交前确认无私有内容。[贡献指南](../../CONTRIBUTING.md)给出本地检查方法。

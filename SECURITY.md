# 安全政策

## 私密报告

请通过 [GitHub 私密漏洞报告](https://github.com/drov-ai/drov/security/advisories/new)提交。该仓库已于 2026-10-17 启用此功能；需要登录 GitHub。入口机制见 [GitHub 官方说明](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)。如果入口不可用，可开一个**不包含漏洞细节**的 issue 请求恢复私密渠道，等待私密入口可用后再发送复现细节；不要公开利用步骤或敏感资料。

报告可包含受影响版本/提交、运行环境、最小合成复现、预期与实际行为、影响范围。不要发送真实 Key、令牌、业务数据库或个人资料。维护者没有已承诺的响应 SLA、赏金或安全认证；修复范围和进展在私密报告中确认，未经约定不公开敏感细节。

## 维护范围

当前为未公开发包的 `0.1.0a0` 开发源码，只维护当前开发线，没有历史稳定版的长期安全支持承诺。发现问题先保留原始证据并停止有风险的新派发，不盲目重试未知外部写入。

本地 SQLite/字节目录不加密；宿主负责文件权限、磁盘和备份保护。Python 工具与 stdio MCP 在宿主权限下运行；Docker 依赖可信 daemon/内核和受测固定 profile，不承诺敌对公网多租户强隔离。E2B 有独立上传/数据保留与费用边界。已发出的模型输入或下载字节无法由本地撤回。

具体威胁、平台与恢复边界见[使用限制](docs/guide/limits.md)和[运行契约](docs/planning/runtime-contract.md)。请仅在自己的受控环境与合成数据上验证问题，不探测他人账号、业务资料或共享基础设施。

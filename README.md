# Drov

Python 通用 Agent 框架，用同一套 Runtime 组织模型、工具、上下文、记忆与人工控制。渐进 API、运行机制一致和上下文可检查是当前设计主张，尚无竞品优势结论。

**源码开发快照 `0.1.0a0`，尚未公开发布 Python 包或官网。** 核心 SDK、Docker 单次 Python/受控文件/CSV 与 PNG、可选 E2B、Responses 适配、MCP stdio、HTTP/实验性工作台及离线维护已有实现。准确范围见[使用限制](docs/guide/limits.md)，任务状态统一见 [progress](docs/planning/progress.md)。

## 开始使用

需要 Python 3.11–3.14 与 uv 0.13.0。在仓库根目录执行，无需 Key 或 Docker：

```sh
uv sync --locked
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

实际工具检索两条合成资料，输出带来源的严格 `Report`；模型回答由离线夹具预设。完整可复制程序、源码安装与每个 extra 见[快速入门](docs/guide/quickstart.md)。PyPI 名称和发布权限尚未核验，当前使用源码或本地构件。

## 从一个工具到完整应用

| 你想做什么 | 从这里开始 |
| --- | --- |
| 检索、严格输出和长资料预览 | [三个参考应用](docs/guide/examples.md) |
| 跨会话 Memory、来源与权限 | [运行原理](docs/guide/concepts.md) |
| 人工输入、审批、未知效果核查 | [Python API](docs/guide/api.md) |
| Responses 模型、stdio MCP 工具 | [模型与 MCP](docs/guide/integrations.md) |
| 本机 HTTP/SSE 与工作台 | [服务与工作台](docs/guide/server.md) |
| CSV → Python → 持久 CSV/PNG | [Docker 与可选 E2B](docs/guide/sandbox.md) |
| 清理、备份/恢复、迁移与授权导出 | [数据维护](docs/guide/maintenance.md) |
| 错误定位、平台和威胁边界 | [排错](docs/guide/troubleshooting.md) / [限制](docs/guide/limits.md) |

Drov 主要能力范围对标 AgentScope 2.0，三个应用检验通用性，不构成产品业务边界。多 Agent、Skill、持久 Notebook/共享工作区等仍为后续，没有可用空壳。工作台为实验性界面。

DeepSeek 固定合成任务真实评测为 smoke7/7、正式78/81、schema73/75、代码9/9，三项模型失败保留；适用配置、证据与限制见[T-16记录](docs/validation/t16-evidence-scope.md)。OpenAI 供应商真实模型未验证。以上不承诺任意任务成功率，体验仅维护者验证。

## 本地文档与开发

```sh
uv sync --locked --group docs
uv run --locked --group docs python scripts/build_docs.py
uv run --locked --group docs python -m http.server 8000 --bind 127.0.0.1 --directory .drov/site
```

站点提供 `0.1.0a0` 版本文档，输出 `.drov/site/` 与本地 zip/hash，不执行部署。构建、版本与 Cloudflare Pages 准备见[托管说明](docs/guide/hosting.md)。开发检查/构建 wheel 见[开发说明](docs/development.md)；接口字段来自[生成参考](docs/reference/README.md)。设计与任务导航见[规划目录](docs/planning/README.md)，跨会话接续见[交接说明](docs/handoff.md)。

欢迎通过 [GitHub Issues](https://github.com/drov-ai/drov/issues/new/choose)提交合成复现或建议；开发流程见[CONTRIBUTING](CONTRIBUTING.md)，漏洞使用[SECURITY 私密渠道](SECURITY.md)。自有代码与文档采用 [Apache-2.0](LICENSE)，依赖/复用声明见[THIRD_PARTY](THIRD_PARTY.md)。现有品牌素材保持原样，公开使用范围待确认，不随本站构建复制。仓库记录域名 `drop.dev` 尚待最终核实，未绑定或上线。

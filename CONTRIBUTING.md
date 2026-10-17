# 贡献 Drov

Drov 是 Python 通用 Agent 框架。开始前阅读 [AGENTS.md](AGENTS.md)、[当前进度](docs/planning/progress.md)和相关任务卡；三个参考应用检验通用性，不限制业务边界。普通缺陷/建议通过 [GitHub Issues](https://github.com/drov-ai/drov/issues/new/choose)，安全问题使用 [私密渠道](SECURITY.md)。

## 准备与检查

使用 Python 3.11–3.14、uv 0.13.0；具体安装见[开发说明](docs/development.md)。在仓库根目录：

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/generate_schemas.py --check
```

HTTP 变更另选 server extra 并检查 OpenAPI；前端改动按[工作台说明](workbench/README.md)重建并验证浏览器。文档变更使用 docs group，运行 `scripts/build_docs.py` 和 `scripts/check_docs.py`。Docker 专项需显式本机 profile，云/模型测试需明确服务、上传范围和预算，普通贡献不需要 Key。测试跳过不能写成对应能力通过。

## 提交有边界的变更

先检查工作区并保护已有变更与品牌素材。公共 API、状态语义、依赖边界或兼容性改变时，同步对应源契约、案例、测试与简短 ADR；普通措辞无需一般规划轮。不要给后续功能添加返回假结果的公共空壳。

围绕实际业务约束、权限和错误路径验证，记录运行环境、通过/失败/未跑范围。文档语法通过不等于 SDK 可运行；预设模型和真实模型质量分开记录。PR 使用仓库模板，给出最小复现、用户行为变化、验证与限制，不提交真实数据、Key、私有 `.env`、SQLite 或云台账。已获许可的上游复用保留版本、来源与声明，见 [THIRD_PARTY](THIRD_PARTY.md)。

自有代码/文档采用 [Apache-2.0](LICENSE)。品牌原件有独立来源与使用范围，不能把代码许可解释为可冒用项目标识。首次贡献可以从可复现缺陷、教程排错和缺失的错误路径检查开始；包/站点公开发布由维护者在具备权限和明确授权后执行。

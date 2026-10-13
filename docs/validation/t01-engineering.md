# T-01 工程骨架验证记录

日期：2026-10-13（Asia/Shanghai）。任务：T-01，关联 C-19。结果：本地工程骨架验收通过；T-02 至 T-18 未开始。

## 交付范围

- src 布局与本地版本 `0.1.0a0`，入口仅 `drov.__version__`；Hatchling 从同一源文件读取版本，包含 `py.typed`。
- pyproject 中的核心/可选/开发依赖声明与 uv.lock；全平台通用锁包含 79 个包条目（含本项目及不同条件分支），不代表核心安装需要 79 个包。
- Ruff、mypy、pytest/pytest-asyncio 配置；1 个真实包级测试与可重复构建/安装检查脚本。
- 无凭据、只读权限、Actions commit 固定的 CI；Apache-2.0 LICENSE、THIRD_PARTY、开发说明。

未实现 Agent/Runtime、工具执行、Memory、适配器、HTTP、工作台或模型评测；extras 仅声明依赖，不是已经支持这些功能。

## 环境和版本

| 项目 | 实际值 |
| --- | --- |
| 系统 | macOS 26.6.2，Apple Silicon arm64 |
| 默认开发 Python | 3.11.9，本地已有解释器；`.venv` 使用该版本 |
| 另一安装验证 Python | 3.14.3，本地 Homebrew 解释器 |
| uv / Hatchling | 0.13.0 / 1.32.4 |
| 核心依赖 | pydantic 2.14.1、aiosqlite 0.22.1、portalocker 4.4.0 |
| 可选依赖 | openai 3.29.0、mcp 2.3.0、fastapi 0.143.0、uvicorn 0.54.0 |
| 检查工具 | Ruff 0.17.0、mypy 2.4.0、pytest 9.1.1、pytest-asyncio 1.4.0、Twine 7.0.0 |
| CI 语法检查 | actionlint 1.7.12，独立工具目录；下载与官方发布校验和比对后使用 |
| Git | 本轮仓库尚无 commit/remote；不能提供虚构的 commit ID |

uv 安装在忽略目录 `.drov/tooling`，未安装到系统 Python；actionlint 在 `.drov/tools`。未读取模型凭据、调用模型或支付 API 费用。安装包及获取官方资料使用了网络。

## 检查与结果

下列命令使用 `uv` 简写；本次实际执行文件为 `.drov/tooling/bin/uv`。

| 方法 | 结果 | 证明范围 |
| --- | --- | --- |
| `uv sync --locked --python 3.11` | 通过 | Python 3.11 的核心/开发依赖安装；锁文件未被隐式更新 |
| `uv run --locked ruff check .` / `ruff format --check .` | 通过 | 当前源文件和格式检查 |
| `uv run --locked mypy` | 通过，4 个 Python 文件 | 当前元数据包、脚本和包级测试的静态类型 |
| `uv run --locked pytest` | 1 passed | 独立进程、仓库外目录的包级导入检查；不是 Agent 业务测试 |
| `uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv` | Python 3.11 通过 | 源码 wheel/sdist 构建、sdist 重建内容一致、两次独立非 editable 安装、实际 site-packages 导入、依赖自洽 |
| 同一检查器指定 `--python /opt/homebrew/bin/python3` | Python 3.14 通过 | 相同构建/重建和两次干净核心安装；未据此声称其他平台通过 |
| `uv sync --locked --all-extras --no-dev --no-editable --python /opt/homebrew/bin/python3`，随后直接运行该环境的导入检查器与 `uv pip check` | Python 3.14 通过 | 可选依赖组合可安装；即使已安装仍不随 `import drov` 加载；未验证其协议行为。检查后恢复默认 3.11 开发环境 |
| `uv run --locked twine check --strict dist/*.whl dist/*.tar.gz` | 通过 | 构件元数据/README 格式，不代表 PyPI 已上传或包名可用 |
| `.drov/tools/actionlint .github/workflows/ci.yml` | 通过 | 工作流语法/表达式本地检查，不代表远程 jobs 已通过 |
| 对照本轮开始时 SHA-256 快照 | 55 个品牌文件内容未变 | 品牌素材保护；Python 构件未包含品牌文件、虚拟环境或凭据文件 |

导入检查关闭 Python 自身字节缓存写入后，拦截应用级文件写入、网络、子进程、线程和常用事件循环创建入口；同时禁止导入可选组件。它不执行模型，也不是通用沙箱或安全审计结论。版本与发行元数据一致，两个构件均包含类型标记及 LICENSE/THIRD_PARTY，未生成虚假的 CLI 入口。

构件在 `dist/drov-0.1.0a0-py3-none-any.whl`、`dist/drov-0.1.0a0.tar.gz`；当次最终构件 SHA-256 由检查器写入 `dist/package-check.json`，这些是本地可重建产物，不提交仓库。不要把本地 alpha 版本号当成公开 release。

## 限制与下一项

CI 已配置 Linux 3.11–3.14、macOS/Windows 3.11/3.14 和非阻塞 3.15 探测，尚未在远程执行。核心依赖当前下界来自实际安装版本；安装与导入不能代替未来 Pydantic/SQLite/锁/MCP/模型协议测试，也不证明整个声明范围均兼容。

从 P4 基线到本轮只改变工程落地与任务状态，没有修改 API v3 或运行行为契约；历史指纹保持原样。已关闭 T-01，下一项 T-02：公共类型、错误/事件/交互、限额与模型能力、适配协议及对应 schema/序列化验证，仍无需模型 Key。

## 来源

构建配置参照 [Hatch 官方文档](https://hatch.pypa.io/latest/config/build/)，依赖与环境参照 [uv 项目配置](https://docs.astral.sh/uv/concepts/projects/config/)，许可字段参照 [Python Packaging 指南](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)。依赖版本取自本日 PyPI 元数据并由实际锁定安装确认，精确源地址和哈希保存在 uv.lock。

CI 上游为 [actions/checkout](https://github.com/actions/checkout)、[astral-sh/setup-uv](https://github.com/astral-sh/setup-uv)；语法检查工具为 [actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)。标准许可来自 [Apache 官方文本](https://www.apache.org/licenses/LICENSE-2.0.txt)，未复制其他 Agent 框架源码。

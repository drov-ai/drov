# 本地开发

T-01/T-02 已完成，包版本为 `0.1.0a0`。提供 Agent 定义、配置与数据类型、工具 schema 和模型适配协议；Runtime、Agent.run、工具执行、Memory 存取、服务与 CLI 均未实现。不要把规划中的运行片段当成可运行教程。

## 环境与安装

最低 Python 3.11，`.python-version` 将开发默认值设为 3.11。包声明允许 Python 3.11 及以上；3.11–3.14 是目标支持矩阵，3.15 只有非阻塞探测，不能仅凭依赖解析成功宣称支持。准确验证范围见 [T-01 记录](validation/t01-engineering.md)。

使用 uv **0.13.0**，项目配置会拒绝不同版本，升级工具须同步验证与记录。可以按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装指定版本。也可只在当前项目建立隔离工具环境，不修改系统 Python：

```sh
# macOS / Linux，在仓库根目录执行；需要 Python 3.11 或以上。
python3 -m venv .drov/tooling
.drov/tooling/bin/python -m pip install uv==0.13.0
export PATH="$PWD/.drov/tooling/bin:$PATH"
```

Windows PowerShell 对应为：

```powershell
py -3 -m venv .drov/tooling
.drov/tooling/Scripts/python.exe -m pip install uv==0.13.0
$env:PATH = "$PWD\.drov\tooling\Scripts;$env:PATH"
```

PATH 修改只针对当前终端；工具目录已忽略，不提交 Git。安装依赖需要网络；这些命令不会调用模型 API，也不需要 Key。

```sh
uv sync --locked
uv run --locked python -c "import drov; print(drov.__version__)"
```

`uv.lock` 锁定开发、核心和可选依赖；默认 sync 安装核心与开发组，不安装模型/MCP/Web extras。PyPI 包名与发布权限尚未核验，当前只使用本地源码或本地构件安装，不提供 `pip install drov` 的公开发布承诺。

## 日常检查

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

格式修改使用 `uv run --locked ruff format .`。T-02 已加入公共类型、工具 schema 和脚本适配协议测试；包级检查仍在仓库外启动独立解释器，核对版本/类型标记和导入行为。测试夹具不调用真实模型、不执行外部工具，不能代表 Agent 任务质量。

生成 schema 与检查漂移：

```sh
uv run --locked python scripts/generate_schemas.py
uv run --locked python scripts/generate_schemas.py --check
```

工具/输出类型子集和实际导出见 [定义参考](reference/README.md)。公开数据经过严格 Python 校验；schema 不能替代跨字段、授权和运行语义检查。

开发工具默认使用已锁定依赖。只有有意升级依赖时才更新 lock，并运行相关验证；不要以删除锁文件处理解析错误。

## 构建与安装验证

```sh
uv run --locked python scripts/check_package.py
uv run --locked twine check --strict dist/*.whl dist/*.tar.gz
```

检查器从源码构建 wheel/sdist，再从 sdist 重建 wheel 并比较内容。它在两个全新环境中分别安装、检查依赖，并从仓库外验证实际 site-packages 导入、`py.typed`、版本与许可证；核心安装不包含 OpenAI/MCP/FastAPI/Uvicorn。

导入检查禁用 Python 自身的字节缓存写入，阻止导入期间应用级文件写入、网络、子进程、线程和常用事件循环创建入口，并阻止可选组件导入。这是针对当前包的回归检查，不是对任意第三方 Python 代码的安全沙箱证明。

验证通过后构件及摘要记录保存在 `dist/`，不提交 Git；不会上传 PyPI 或运行发布。只需生成构件时可使用 `uv build --no-sources`。对另一解释器验证可运行：

```sh
uv run --locked python scripts/check_package.py --python /path/to/python
```

`openai`、`mcp`、`server` extras 目前仅声明未来适配器的依赖。安装它们不代表已能创建对应 Drov 客户端。`uv sync --locked --all-extras` 只用于依赖组合检查，仍不读取或调用模型 Key。

## CI 与变更记录

CI 使用只读仓库权限，无发布或凭据步骤；Actions 固定到上游 commit，uv 与本地版本一致。Linux 配置 3.11–3.14，macOS/Windows 各配置 3.11/3.14；另有非阻塞 3.15 探测。工作流尚未在远程运行，不能将“已配置”写成“跨平台全部通过”。

每轮按 [协作规则](../AGENTS.md) 更新进度、受影响契约和验证记录；新会话按 [交接说明](handoff.md) 恢复工作。上游依赖与源码复用入口见 [THIRD_PARTY](../THIRD_PARTY.md)。下一项以 [progress](planning/progress.md) 为准。

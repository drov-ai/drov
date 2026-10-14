# 本地开发

包版本为 `0.1.0a0`。提供定义/类型/schema、模型协议、事务存储、Runtime 生命周期、Agent 执行、Python 工具/权限、上下文预览/清单、事务 Memory，以及审批/输入、外部未知效果、verify/recover。T-10A 首批新增私有字节暂存与固定分析镜像，见 [准备工具与边界](../sandbox/README.md)；PythonSandbox、Runtime 文件/ready/授权读取路径仍未接入，真实模型适配与服务/CLI 仍未实现。实际验收状态以 [progress](planning/progress.md) 为准，T-10 故障矩阵见 [验证](validation/t10-faults-cancellation.md)；不要把规划片段当成可运行教程。

## 环境与安装

最低 Python 3.11，`.python-version` 将开发默认值设为 3.11。包声明允许 Python 3.11 及以上；3.11–3.14 是目标支持矩阵，3.15 只有非阻塞探测，不能仅凭依赖解析成功宣称支持。准确平台与跳过范围见 [T-10 记录](validation/t10-faults-cancellation.md)。

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

格式修改使用 `uv run --locked ruff format .`。T-02 已加入公共类型、工具 schema 和脚本适配协议测试；包级检查仍在仓库外启动独立解释器，核对版本/类型标记和导入行为。测试夹具不调用真实模型；T-04/T-05 实际运行任务、工具与可释放线程检查权限、队列、记录和所有权，T-06 接入可实际执行的脚本 Agent 与合成检索示例，仍不能代表真实模型任务质量。

生成 schema 与检查漂移：

```sh
uv run --locked python scripts/generate_schemas.py
uv run --locked python scripts/generate_schemas.py --check
```

工具/输出类型子集和实际导出见 [定义参考](reference/README.md)。公开数据经过严格 Python 校验；schema 不能替代跨字段、授权和运行语义检查。T-03 同时生成 storage-schema-v1.sql 供检查内部布局，初始化必须使用 SQLiteStorage.open。

开发工具默认使用已锁定依赖。只有有意升级依赖时才更新 lock，并运行相关验证；不要以删除锁文件处理解析错误。

## 构建与安装验证

```sh
uv run --locked python scripts/check_package.py
uv run --locked twine check --strict dist/*.whl dist/*.tar.gz
```

检查器从源码构建 wheel/sdist，再从 sdist 重建 wheel 并比较内容。它在两个全新环境中分别安装、检查依赖，并从仓库外验证实际 site-packages 导入、`py.typed`、版本与许可证；核心安装不包含 OpenAI/MCP/FastAPI/Uvicorn；随后在仓库外用隔离解释器运行普通/流式离线检索、上下文预览、七进程 Memory 及四进程审批/输入及独立服务写入/核查示例，验证安装后的执行能力。

导入检查禁用 Python 自身的字节缓存写入，阻止导入期间应用级文件写入、网络、子进程、线程和常用事件循环创建入口，并阻止可选组件导入。这是针对当前包的回归检查，不是对任意第三方 Python 代码的安全沙箱证明。

验证通过后构件及摘要记录保存在 `dist/`，不提交 Git；不会上传 PyPI 或运行发布。只需生成构件时可使用 `uv build --no-sources`。对另一解释器验证可运行：

```sh
uv run --locked python scripts/check_package.py --python /path/to/python
```

`openai`、`mcp`、`server` extras 目前仅声明未来适配器的依赖。安装它们不代表已能创建对应 Drov 客户端。`uv sync --locked --all-extras` 只用于依赖组合检查，仍不读取或调用模型 Key。

## CI 与变更记录

CI 使用只读仓库权限，无发布或凭据步骤；Actions 固定到上游 commit，uv 与本地版本一致。Linux 配置 3.11–3.14，macOS/Windows 各配置 3.11/3.14；另有非阻塞 3.15 探测。各任务的准确提交及矩阵结果见验证记录；T-10 最新远程状态见 progress 和故障/取消验证，不把旧 CI 用作新代码通过证据。

每轮按 [协作规则](../AGENTS.md) 更新进度、受影响契约和验证记录；新会话按 [交接说明](handoff.md) 恢复工作。上游依赖与源码复用入口见 [THIRD_PARTY](../THIRD_PARTY.md)。下一项以 [progress](planning/progress.md) 为准。

## 首版代码沙箱的准备时点（计划）

当前没有 drov.sandbox/drov.artifacts 或 E2B adapter；普通 SDK 开发不需要 Docker。按 [ADR-0016](decisions/0016-sandbox-artifacts-and-backends.md)，T-10A/T-10B 实施 Docker Python、受控文件与持久 CSV/PNG 产物，T-10C 独立可选 E2B。Docker Desktop/官方标准库镜像与显式 seccomp 已做基础准备，见 [环境记录](validation/sandbox-environment-preparation.md)，用户保持 Desktop 运行即可；数据分析镜像和私有存储由执行者在 T-10A 准备、锁实际版本/digest，当前标准库镜像没有分析库。

不挂宿主凭据/仓库/socket，不在执行时拉镜像/安装，不用宿主回退。默认/平台/文件控制见 [工程方案第 9 节](planning/engineering.md)，接口在 [API 草案](planning/api-draft.md)，未跑真实组不能 skip 关闭两卡。下一项以 progress 为准，New Chat 交接见 handoff；忽略目录的准备文件仅当前 Local 存在。

E2B 账号/固定 template-build/区域权限、本地 E2B_API_KEY 凭据引用、合成数据上传许可与单独沙箱预算只在 T-10C 远端验收前需要；模型服务/model ID/Key/共同模型预算只在 T-11/T-16 live 前需要。当前不创建这些资源或调用付费 API；无 E2B 资源不阻塞 Docker 首版，细则见工程第 10 节和维护发布策略。

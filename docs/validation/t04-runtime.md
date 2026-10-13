# T-04 注册与资源所有权验证

- 日期：2026-10-13（Asia/Shanghai）
- 开发版本：0.1.0a0；准确代码提交/远程 CI 状态见 [progress](../planning/progress.md) 与 Git 记录
- 起点：main / 4b8e478；工作区干净，T-03 实现 751ff55 及 CI 证据已核对
- 契约：P4 v3 + [ADR-0008](../decisions/0008-runtime-ownership.md)；历史 baseline JSON 原样保留，源契约新增差异由 ADR 说明
- 本地环境：macOS arm64，Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0；uv 0.13.0，uv.lock 不变，无新依赖

## 验收证据

| T-04 要求 / 关联 | 实际验证 |
| --- | --- |
| 注册 / B-07 | 缺身份和重复 name/revision 拒绝；同名不同 revision 可注册；构造及注册不创建数据库、不执行模型/工具 |
| 指纹与 schema / B-07 | 声明配置规范 JSON/hash，字典顺序不影响；独立进程相同定义同 hash；指令/模型配置/限额/必要依赖差异有新指纹；修改工具 schema 拒绝 |
| 恢复接点 / B-07 | 保存真实 SQLite RunRecord，关闭并重开后原 revision/hash 通过；缺原 revision、不同配置、未来 record schema 拒绝；校验不派发任何工作 |
| 定义变更与所有权 | 注册后配置变更拒绝；同配置新模型对象虽有相同跨进程指纹，在原 Runtime 内替换仍拒绝，防止漏装配/漏清理 |
| 装配 / X-02、B-12 | 存储→模型→依赖装配，逆序清理；自有模型/依赖关闭，默认借用模型/依赖/已打开存储保留；同对象别名只关一次，冲突所有权拒绝 |
| 启动失败 / X-02、B-12 | 部分打开的存储、后续工厂失败、必要依赖缺失均清理；关闭器故障保留较早资源和真实 SQLite 锁，修复后可重试关闭 |
| 生命周期取消与并发 | 重复取消 open/aclose 等待不丢刚创建的资源；并发 close 共用任务；启动中关闭先等待工厂返回；执行/生命周期回调自我关闭拒绝，避免等待自身 |
| 任务所有权 / B-06 | 等待方取消、删除工作句柄并 GC 后实际任务仍存在并继续；工作异常被内核观察，后来 wait 仍得到原异常 |
| 容量 / B-12 | 20 个并发预留仅容量内成功；调度失败释放，重复/外部槽位拒绝；未交接的接收槽位阻止资源关闭；取消前尚未派发时执行数 0 |
| 基础取消 / B-06、B-12 | 显式请求后 checkpoint 禁新派发；拒绝停止任务继续占容量；关闭超时停止新接收并保留存储锁；实际结束后释放 |
| 未停止调用 / B-12 | 真实 ThreadPoolExecutor 调用由原 concurrent Future 登记；包装 Task 已取消/结束仍保留槽位和 SQLite 锁；线程屏障释放后才关闭并允许另一存储打开 |
| 已派发调用的竞态 | 取消信号已到时仍登记已派发 Future，避免丢引用；包装任务返回而异步调用未结束同样保留槽位 |
| 即时调度 | Python 3.14 的 eager_task_factory 下先持有任务再进入调用代码，真实登记/关闭通过；3.11 不具备该 API，明确跳过 |

新增 37 项测试（tests/test_runtime.py 24 项、tests/test_runtime_work.py 13 项），共收集 205 项。Python 3.11：204 passed / 1 skipped（仅 eager_task_factory）；Python 3.14：205 passed。Ruff、格式、严格 mypy 和生成 schema 检查通过。

## 检查命令

```sh
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked pytest
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
```

构建验证包括源码 wheel/sdist、sdist 重建 payload 对照、各解释器两个干净核心环境安装/依赖兼容/无副作用导入；不上传包。文档相对链接、说明片段语法、能力/案例引用、任务依赖、55 份品牌素材摘要、旧 baseline 和 lock 原样保留另外核对；检查明细存本地 .drov/t04-doc-check.json。

## 实际发现并修正

- 包装任务取消后实际线程继续运行：登记原 concurrent Future，并延迟释放活动槽位/资源。
- 取消信号与调用登记相遇时，不能因 checkpoint 拒绝而丢弃已经派发的调用；派发前检查，派发后必须留引用。
- 即时调度可能在 work.task 赋值前进入用户代码：增加所有权交接屏障，Python 3.14 回归验证。
- Agent 替换同配置模型对象不会改变跨进程 hash：额外固定原 Runtime 的模型对象身份，避免新对象绕过生命周期。
- 关闭器超时/失败或从生命周期回调自我关闭：保留关闭任务与剩余资源栈，允许外部重试并拒绝自我等待。

## GitHub CI

T-04 本地验收已完成；本轮同步后的准确实现 commit、CI 链接、通过/跳过项在此补记。T-03 的 CI 成功不算本轮新代码的 CI 证据。

## 限制与后续

T-04 完成整张任务卡；C-12、X-02 和 B-06/07/12 的跨入口/状态部分仍需要后续任务。没有 start/run/wait/cancel/recover 公共执行入口，不返回假完成结果。私有取消不是已持久化 cancel_requested；恢复检查不扫描/推进遗留 Run。受控真实线程是生命周期证据，不是 Python 工具执行器或外部副作用恢复证明。

工厂负责返回前的部分清理与启动限时；Runtime 无法抢占自定义回调中不合作的同步代码。未声明的客户端内部配置/任意函数体/远端行为仍依赖开发者更新 revision；model_config 不得放真实凭据。借用对象须由宿主维持生命周期。权限引擎、工具执行/Memory 工具、Agent 循环、真实模型/MCP、HTTP 和维护仍未实现。

下一项 T-05 Python 工具与权限，前置 T-02 已验收；随后 T-06 依赖 T-04/T-05。T-05 使用合成工具和身份夹具，无需 API Key、业务数据或发布账号。建议继续本会话完成 T-05；T-06 验收后再按上下文情况交接，复杂任务仍可单独开启会话。

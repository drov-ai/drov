# T-05 Python 工具与权限验证

- 日期：2026-10-13（Asia/Shanghai）
- 开发版本：0.1.0a0；准确实现提交与远程 CI 状态见 [progress](../planning/progress.md) 和 Git
- 起点：main / e8588a7，工作区干净；T-04 的实现/CI 与资源契约已核对
- 契约：API/运行 v3 + [ADR-0009](../decisions/0009-python-tools.md)；历史 P4 baseline/品牌/锁文件不覆盖，无新依赖
- 本地环境：macOS arm64，Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0，uv 0.13.0

## 验收证据

| T-05 要求 / 关联 | 实际验证 |
| --- | --- |
| @tool、schema / C-02 | 真实同步/异步函数返回结果，共用 codec；默认 revision=1/unknown/policy；生成器、保留 Memory 名称拒绝；同 schema 工具对象替换拒绝 |
| 参数与上下文 / B-10、S-03-C | 默认值规范化后进入摘要/授权；ctx/tenant 等额外注入、重复 JSON 键、NaN、bool 当 int、错误类型拒绝，函数计数 0；实际 ToolContext 归属/Run/operation/deps 正确 |
| 权限顺序 / C-08、B-01/04 | 9 组顺序组合及 scope/resource 名称校验；业务否决、deny、required approval/ask 不被 allow 覆盖；unknown/write 默认 ask，显式 allow 也不会开放未支持实际派发 |
| 授权动态性 / S-03-A/C | 首次、队列等待后、派发提交后撤权均计数 0；授权器改其参数快照不改实参/下一次检查；异常和超时安全拒绝；伪造 owner 无法查到 Run |
| 重复与并发 | 同规范默认值重试返回已保存事实，不再调用；同 ID 不同输入或活跃并发冲突；ASK 无虚假 request_id，也不能以重复调用充当批准 |
| 只读重试 / S-01-B | 0/1/2 额外重试按 TransientToolError 有界执行，普通异常只执行一次；每次独立 attempt_id、重新授权并预留预算 |
| 返回边界 | 类型不符/NaN/超限和未预期 coroutine 为 output_unavailable、不重试；coroutine 关闭且不擅自 await；None 真实成功 has_result=True |
| 原子记录 / C-02、B-03 | 操作/Run 摘要、状态与检查点版本、事件同事务；派发标记提交前失败实际计数 0；线程提交失败记录未执行并释放预留 |
| 预算 | tool_dispatch_count 同 dispatched 提交，第二操作不能重置 max_tool_calls；计数为保守预留，提交后取消/撤权可能没有实际调用，但不退预算 |
| 存储 / B-10 部分 | 真实 SQLite 关闭重开后记录/结果仍可读，同操作不重派发；不是完整 Agent 恢复工作流 |
| 有界队列 | 线程=1、队列=1 的实际线程/排队/拒绝实验，第三请求不派发；排队撤权或取消不执行，取消释放预留后新请求可继续 |
| 超时/取消 / B-06/12 | wait 取消不取消工具，显式工作取消协作停止；实际线程超时后继续占槽位与 SQLite 锁，不能关闭或被另一实例拿走；迟到结果/异常不变成成功 |
| 不合作异步调用 | 拒绝取消的真实 asyncio Task 在包装工作结束后仍占槽位，关闭报 ShutdownIncomplete；释放屏障后才关闭 |

新增 52 项测试（permissions 11、tool_execution 41），共收集 257 项。Python 3.11：256 passed / 1 skipped（eager_task_factory 为 3.12+ API）；Python 3.14：257 passed。Ruff、格式、严格 mypy、生成 schema 及文档/构建检查结果见本轮实际日志。

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

构建检查包含 wheel/sdist、sdist 重建 payload、两个解释器各两个干净核心安装及无副作用/可选依赖隔离导入；不上传包。相对链接、片段语法、能力/案例编号、18 项任务/25 条依赖、55 份品牌素材摘要、历史 baseline 和 uv.lock 另行核对，明细保存在本地 .drov/t05-doc-check.json。新增公共 schema 为 PermissionPolicy/Decision/ToolResource，内部 operation payload 单独 schema=1。

## 本轮修正

- 排队时授权可能撤销：队列由内核持有，在实际工作位置和派发标记提交后重新查当前授权。
- 不能让授权器改写实际参数：每次从固定 JSON 快照重建资源，可信 ToolContext 单独绑定。
- 包装 Future 超时后迟到异常可能无人观察：登记原 Future 并观察包装 Future，保持资源直至实际停止，迟到输出不用。
- 返回了 coroutine 不能将其偷偷当第二工具执行：关闭并记输出不可用。
- 工具对象替换、无限线程排队与提交故障：固定注册身份、有界预留、真实 Future 所有权和未执行记录。
- 实际异步工具在子 Task 中运行，自我等待 Runtime 关闭会相互等待；关闭检查扩展到已登记的实际调用，拒绝自关闭而不误取消父工作。

## GitHub CI

实现检查点为 d84c66f；最终修正提交：[ecc734b](https://github.com/drov-ai/drov/commit/ecc734ba595a43579172d7631153925052962208)。[CI 38396712936](https://github.com/drov-ai/drov/actions/runs/38396712936) 验证该最终实现，10 个 job 全部 success。quality 完成格式/Ruff/mypy/schema，八个标准平台 job 完成测试、wheel/sdist、重建/干净核心安装、twine 与 all-extras 导入；没有 Key/live 调用。

| 平台 / Python | pytest 实际结果 |
| --- | --- |
| Linux 3.11 | 256 passed / 1 skipped（即时任务工厂 API 不适用） |
| Linux 3.12 / 3.13 / 3.14 | 各 257 passed |
| macOS 3.11 | 256 passed / 1 skipped（即时任务工厂 API 不适用） |
| macOS 3.14 | 257 passed |
| Windows 3.11 | 255 passed / 2 skipped（即时任务工厂 API；平台不允许替换已打开 SQLite 文件） |
| Windows 3.14 | 256 passed / 1 skipped（平台不允许替换已打开 SQLite 文件） |
| Python 3.15 advisory | 257 passed；不扩大支持矩阵 |

本轮随后补记 CI 的文档提交不改变已验证代码，不称作另一次完整矩阵验证。

## 限制与后续

T-05 整张任务卡完成；C-02/C-08 和 S/B 的完整 Agent/交互部分仍需后续验收。只读工具声明不等于沙箱或后端安全证明。Caller 必须在 T-06 完整模型计划提交后才接入私有派发器；目前没有 Agent.run、Runtime.start、实际 HTTP 认证/正文投影，也没有消费批准/核查的入口。write/unknown 不执行，ASK 不伪造可恢复交互；业务写入的未知效果保证仍在 T-09/T-10。

当前工具错误只给安全代码，不展示任意异常正文。Run 总活动时间、模型预算、历史/结果流一致性和 Run 收尾由 T-06 接入；本轮 tool_dispatch_count 只证明派发预算不重置。已有操作返回事实，不自动恢复未完成操作。未来旧 payload/数据迁移属于 T-15。

下一项 T-06：只读离线循环、start/run/wait/stream/events/inspect、授权投影、历史提交、限额与检索例子。无需 Key/业务数据。建议为较大的闭环集成开启 New Chat，在同一 Local checkout 接续；用户真实模型服务/model ID/本地 Key/预算只在 T-11/T-16 live 前准备。

# T-10 硬终止、提交故障与取消竞争验证

- 日期：2026-10-14（Asia/Shanghai），开发版本 0.1.0a0。
- 起点：Local `/Users/yanlong/projects/drov`，main / 53cee41，干净且与 origin/main 同步。按 progress 核对 T-09 已整卡验收，未跳过前置。
- 前置复跑：Python 3.11.9 的 491 passed / 2 skipped；Ruff、格式、严格 mypy（72 files）、schema 全通过。
- 环境：macOS 26.6.2 arm64，uv 0.13.0；Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0。
- 交付：[进程夹具](../../tests/runtime_process_fixture.py)、[硬终止矩阵](../../tests/test_runtime_crashes.py)、[取消/真实 I/O 故障](../../tests/test_runtime_faults.py)，独立 [事项服务](../../examples/item_service_fixture.py) 增加写后回包前屏障。
- 本轮没有修改 SDK、公共接口/schema、依赖锁、SQLite 持久 DDL 或历史 fingerprint；无需新 ADR。T-10A/T-10B 没有开始。
- 本地全库：Python 3.11 **611 passed / 2 skipped**（93.10s）；Python 3.14 **613 passed**（99.03s）。两项 skip 为仅 Python 3.12+ 可用的即时任务工厂。Ruff/格式、严格 mypy（75 files）、schema、两解释器构建/安装/Twine 全通过；远程待本轮提交后核对，不用历史 CI 关闭整卡。

## 矩阵与断言

新增 120 项：97 项硬终止/重开，23 项两后端取消竞争、部分计划及真实 SQLite 错误。全库 613 项，不撤掉已有 493 项。模型使用自有 FixtureModel（ScriptedModel 子类）和 ScriptedModel，固定计划/响应；Usage 的 token 和金额是合成数值，既不是实际模型用量也不是已付费用。

| 组 / 数量 | 屏障与实际断言 | 映射 |
| --- | --- | --- |
| 11 个提交边界 × 前/后 × SDK/stream = 44 | accepted、model_input/context、model attempt、完整工具计划、prepared、审批请求、响应回执、dispatched、成功结果、完整最终响应、终态；父进程收到目标事务屏障后 kill SDK，新 SDK 进程重开 | B-03/04/05/07，S-03-B/D |
| 同 11 边界 × 提交前错误/提交后丢应答 = 22 | 真实 SQL 提交前注入错误，或真实 COMMIT 成功后注入 OperationalError；调用方仅得 PersistenceUnavailable，无虚假成功回执；重开依据实际数据库结果恢复 | B-03/04/05 |
| 同 11 边界 × 实际拒绝 COMMIT = 11 | SQLite 的连接局部 TEMP 表加入延迟外键违反，SQLite 自身抛 SQLITE_CONSTRAINT_FOREIGNKEY；事务回滚，没有新持久表/DDL；错误名称独立核对 | B-03/04/05 |
| 实际调用窗口 = 3 | 服务先写账本再在 stdin 屏障等待回包，SDK 被 kill 后服务继续存活；模型 attempt 已开始、完整模型响应已返回控制器但未提交也分别 kill；工具计划未提交时写入 0 | B-05/07 |
| 非协作调用 = 4 | 真实线程/拒绝取消的 Task，各按释放屏障或硬终止 SDK 收尾；ShutdownIncomplete 时槽位 1、同进程和另一个 SDK 探针进程均不能取得 SQLite 锁；真实停止后槽位 0/新进程可重开 | B-05/12，X-02 的 SDK 资源子范围 |
| 取消请求提交前/后 = 2 | cancel_requested 与事件一起回滚/保留；后者重开显式重复 cancel 完成尚未收尾的取消，无写入；前者原审批可继续，写入 1 | B-03/04/05 |
| 两调用部分成功 × 前/后 × SDK/stream = 4 | 原两操作 ID 与顺序保存；第一写已实际发生，恢复复用提交结果或核查，不重复第一写；第二个原操作独立审批，最终总 writes/attempts 2 | B-05/07，S-03-B/D |
| Memory 提交前/后硬终止/丢应答/真实失败 = 5 | Memory、结果、事件、tool_dispatch_count 一起回滚/保留；重开恰好一个 version=1、一个 memory.changed 和一个工具预算，外部写入 0 | B-03/07/09，S-02-A |
| 已耗尽次数/活动时间硬重启 = 2 | model_retries=0 或已预留全部活动时间；recover 不调用新模型，failed/model_attempt_limit 或 limit_exceeded，step/attempt 保持 1，未知用量不填 0 | B-07，C-11/15 |
| 五个取消完成次序 × 两后端 = 10 | 派发前、结果提交前/后、最终响应提交后但终态前、终态提交后分别显式 cancel；20 个重复 cancel 至多一个取消事件；已发生写入、成功结果、完整已知用量保留；核查收尾无新模型/写入 | B-04/05/06 |
| 等待/流断开 × 两后端 = 4 | 已批准的真实调用继续持有槽位，取消 wait 或退出订阅不写 cancel_requested；释放调用屏障后 completed 且写入 1 | B-06，C-12 |
| 部分计划取消 × 两后端 = 2 | 第一成功写入保留，第二审批尚未派发时取消，succeeded/not_executed，模型调用 1，实际写入 1 | B-04/05 |
| 实际只读/满盘 = 3 | PRAGMA query_only 使 SQLite 抛 SQLITE_READONLY（派发前、写后）；限制 max_page_count 后大结果使 SQLite 抛 SQLITE_FULL；未提交结果无 succeeded/completed，写前 0/写后 1，解除故障后显式恢复/核查 | B-03/05 |
| 接收提交 × 前/后 × 断开/cancel = 4 | COMMIT 内取消 respond 等待者两次仍不终止受所有权保护的控制事务；显式 cancel 与接收串行，事务保留唯一 consumed 交互/回执；20 次重放响应完全相同且不新增调用 | B-03/04/06 |

每个数据库恢复检查同时核对 `PRAGMA integrity_check`、Run/checkpoint/最后事件版本、无断点事件序号、Session 活跃引用和事务原子事实。计数服务有独立进程、非幂等行为、原 tenant/user/operation 的查询记录；杀 SDK 不清零副作用。一次操作恢复后最终 writes/attempts 恰好 1；dispatched 已提交但还未真正调用时，核查 not_applied 后使用新审批/attempt，累计 dispatch=2，真实写仍 1。服务已经执行时仅凭 applied 证据/已提交结果继续，不重派发。

保存模型输入的重试逐字段相等，包括 call ID、消息、schema、完整上下文。step 不重复计数，attempt 增加；模型响应未提交的总用量/金额保持 unknown，已知完整路径合成总 token=10、金额=0.02 USD。完整最终响应已经提交时不再次调用模型。重放旧批准与不可变保存回执相同，Run state/version 与模型调用数不变。已有撤权、参数绑定、配置/revision/依赖缺失、20 次响应去重、超时、迟到 verifier 和未知预算核查回归全部保留。

## 检查命令与构件

```sh
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/t10-dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/t10-dist-py314/*.whl .drov/t10-dist-py314/*.tar.gz
git diff --check
```

完整回归日志：.drov/t10-final-py311.log / t10-final-py314.log；构建与干净安装日志：.drov/t10-package-py311.log / t10-package-py314.log。构建产物在 dist/ 与 .drov/t10-dist-py314/，均忽略且未上传。

两解释器各一次 wheel/sdist 与从 sdist 重建的 payload 一致性；各两套干净核心安装、兼容/隔离导入及五示例普通/stream，共四套干净安装。operations_offline 仍逐例核对独立服务与新 SDK 进程；事项服务新增 barrier_reply 模式未改变普通示例。Twine 四构件均 PASSED。新增测试/夹具由现有 sdist 的 tests 目录包含；新验证说明与进度补记晚于本地构建，远程 CI 需对实际提交重新构建。

## 调查中失败与修正

第一批 57 项通过后扩展矩阵，夹具出现三类错误，均修正并留下日志：取消标志提交后重开仍处于原审批，夹具不能 resume 已取消的请求，需重复显式 cancel 完成原收尾；applied 提供有效结果后操作会变成 succeeded，不能继续断言 unknown；query_only 禁止 BEGIN IMMEDIATE，不能直接用原 Storage 读取，改为真实数据库事实核对。SQLITE_FULL 会使 SQLite 自行回滚且让原连接保守停止使用，测试通过正常有界关闭重试取得新所有权，不修改 `_poisoned` 绕过失败。另修正类型收窄/格式检查。没有以重跑消除非确定性失败，也没有修改 SDK 来放宽断言。

局部记录在 .drov/t10-crashes-first.log、t10-crashes-second.log、t10-races-first.log、t10-races-second.log、t10-io-first.log、t10-io-second.log、t10-io-final.log、t10-ownership-cancel.log、t10-real-commit-first.log、t10-extra-first.log、t10-receiving-first.log。局部旧通过不替代最终完整回归。

## 适用范围与后续

- 证明的是当前 SDK 的本地/平台矩阵中的故障安全性；用固定脚本模型验证运行机制，不证明真实模型质量或竞品优势。硬终止是进程 kill，未模拟断电/设备损坏；外部账本服务存活，未宣称通用 exactly-once。
- 故障提交包含注入应答错误、SQLite 真实约束拒绝及只读/页数耗尽；不宣称在真实用户磁盘上制造物理满盘。SQLite FULL 后可能需要有界关闭重试，不把仍持有资源的首次 ShutdownIncomplete 写成已经关闭。
- stream 组验证流式接收入口；继续执行通过同一 respond/resume/recover 控制器。HTTP 断连、真实 MCP/HTTP 资源生命周期仍分别由 T-12/T-13 验收，不从本轮 X-02 的 SDK 子范围推断完成。
- 旧备份回退、restore barrier、维护仍待 T-15。Python 线程不能强杀，未停止调用仍保留所有权；核查事实由宿主/获授权核查者负责。
- 下一项是 T-10A：显式 Docker/Linux 配置预检、固定本地镜像、代码专用工具审批/执行键、实际逐调用容器与有界结果的离线闭环。T-10B 才完整验收隔离/资源/清理/崩溃，缺真实后端不能关闭沙箱卡。用户准备与会话建议由 [progress](../planning/progress.md) 维护。

# T-09 审批与补充输入子范围验证

- 日期：2026-10-14（Asia/Shanghai），开发版本 0.1.0a0
- 起点：`/Users/yanlong/projects/drov`，main / d9bafbc，工作区干净，与 origin/main 同步
- 前置：阅读 AGENTS/progress/规划导航/handoff、T-08 与范围记录；复跑旧 400 项：398 passed / 2 skipped，静态/格式/严格类型/schema 通过；T-08 无剩余验收缺项
- 环境：macOS arm64，uv 0.13.0；Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0
- 契约：API/运行 v3 + [ADR-0014](../decisions/0014-durable-approval-input.md)；无新依赖、公共 DTO/schema 或 SQLite DDL，uv.lock/品牌/历史 P4 fingerprint 保留
- 状态：审批/输入子范围本地验收；**T-09 整卡未完成**。未实现外部 write/unknown、reconciliation、verify/recover/启动扫描；无真实模型、密钥读取、付费调用、公开发布或部署

## 本地证据

新增 [test_interactions.py](../../tests/test_interactions.py) **48 项**。更新两条历史断言：显式 Runtime 的 Memory ASK 现在暂停审批，保留未写入/预算 0 断言；Runtime.respond/resume 已有实际实现，不再断言它们不存在，verify/recover 空壳拒绝断言保留。全库 **448 项**。

| 子范围 / 关联 | 实际证据与限制 |
| --- | --- |
| 原计划继续 / C-11、B-04 | 批准后只执行固定参数/原 operation；两调用逐个审批、第二个请求不同；重复第一响应等待当前第二请求，不重问模型、不重做第一个 |
| 原子响应/拒绝 / C-09 | 交互消费、不可变回执、操作结果或批准引用、事件和 running 同事务；拒绝是 approval_denied 工具结果，独立真实 read 工具计数 0，模型可解释 |
| 重复/冲突 / B-04 | 两后端 20 次并发相同批准只一回执/一次工具执行；Memory 同样 20 次、版本 1、memory.changed 仅一次；冲突响应拒绝，终态/后来到期相同响应仍返回旧回执 |
| 容量/校验 / B-04 | 唯一活动槽位被实际模型 Task 占用，响应失败请求/回执不变；错误 request、响应 kind、身份/tenant、Agent 配置/revision、保存的参数绑定变更均不消费、不派发 |
| 身份/正文 / C-08 | Bob 只有 respond 可响应，resume 因 read/view_content 不足在消费前拒绝；执行 ToolContext/动态权限仍 Alice；撤销 Bob 响应权限后重复也拒绝；明确授予后同 tenant 查看可用，元数据不泄露 prompt/答案 |
| 当前业务否决 / C-08 | 批准后撤销原执行权限，调用计数 0；HumanInput 答案消费时撤权不消费；approve 不授予执行者权限 |
| 到期 / C-09 | 控制/inspect 触发待审批到期并 failed/interaction_expired，无派发/回执；已消费回执到期仍可返回；批准消费后、真正派发前期限到达，旧批准不使用 |
| HumanInput / S-03-E 子路径 | 注册 dataclass schema、prompt 参数；缺字段/错误类型/注入 user_id 可修正；合法值是原调用结果，再产生固定只读审批；ask 策略先审批再输入，不恢复 Python 栈 |
| 临时入口 / B-01 | Agent.run 遇到 HumanInput 或审批 failed/interaction_unavailable，无 request_id，待授权调用 0；显式 MemoryStorage Runtime 则可保存交互，不按后端类型误判临时 |
| 预算 / B-07 子范围 | 多次暂停保留 model_step_count/model_attempt_count/tool_dispatch_count；两 step 上限在响应后仍阻止第三次模型调用；输入结果计一次工具用量 |
| 取消 / C-12 | 两后端 approval/input 的取消/响应竞争，至多一回执、至多一次只读派发、最终 cancelled；已成功 Memory 写入在后续待审批取消后保留版本/正文，旧请求无效 |
| 提交/调度失败 / B-03/07 子范围 | 在响应事务最终 save_step 注入已知回滚：无回执/消费/派发，槽位释放；提交后调度失败保存 receipt 并 suspended/recovery，重复只返回原回执；完整 recover 尚未实现 |
| 跨 Runtime/SDK 流 / B-07 子范围 | SQLite 关闭/重开缺原 revision 不消费，重建后响应原请求；重新订阅包含旧 suspended 的完整事件仍读至当前 completed，流不在旧暂停结束 |
| 实际跨进程示例 | [interactions_offline.py](../../examples/interactions_offline.py) 普通/stream 各批准与拒绝两案例，每例四个独立进程：HumanInput → 答案/原 read_item 候选 → 批准/拒绝 → 重复原响应；核对不同 PID、实际原结果、工具计数 1/0、原回执及重复时模型 0 |

最终本地 Python 3.11：**446 passed / 2 skipped**（Python 3.12+ 即时任务工厂 API）；Python 3.14：**448 passed**。Ruff/格式/严格 mypy（66 source files）/公共及存储 schema 漂移通过。T-08 的 56 项 Memory 与原有真实线程/存储进程回归保留。

早期失败：新增模块格式/类型问题、两条已过期范围断言，以及示例 ScriptedStep 参数名错误；同轮修正后才运行上述最终检查。未把中间失败当通过，也未把只读/Memory 计数当完整外部写入保证。

## 检查命令与安装证据

```sh
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python examples/interactions_offline.py
.drov/tooling/bin/uv run --locked python examples/interactions_offline.py --stream
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
git diff --check
```

两解释器均完成 wheel/sdist、从 sdist 重建 wheel 并比较 payload；各两个干净核心环境安装/兼容/隔离导入，以及检索/预览/Memory/交互四个示例普通与 stream，共四次干净安装通过。每次交互示例运行八个独立子进程，两种入口各校验批准/拒绝及重复回执；构件在 dist/ 和 .drov/dist-py314/，检查日志为 .drov/t09-package-py311.log / t09-package-py314.log；Twine 四个构件全部通过，未上传。源文档 45 文件、357 个本地链接、14 个 Python 片段 AST 检查与 Git 差异通过；受保护文件无变更、本轮差异凭据模式计数均 0，结果 .drov/t09-doc-check.json。模式检查不证明发现所有秘密，不读取环境文件/真实凭据；AST 检查不等同 SDK 运行。远程 CI 随本轮提交另记，旧 CI 不代表新代码通过。

## 剩余范围与恢复步骤

1. 外部 write/unknown 仍接收前拒绝，没有 result verifier 注册、核查建议或 applied/not_applied/unresolved 消费。完整 S-03-A/B/C 的外部业务写入、S-03-D/B-05 未实现；当前 read_item 例子与 Memory 本地写入仅交付机制子范围。
2. recover 和启动扫描未实现：accepted/running 无本地所有者仍要求显式恢复；已消费响应后调度失败留下的 recovery 记录不可通过重复批准推进。下一子范围依据原计划/检查点/回执继续实现，不能手改记录/重新调用写入来代替恢复。旧无 pending_plan 的引擎记录只承诺查询兼容，恢复兼容另验收。
3. 普通关闭/新进程响应待审批已实际验证；尚未运行 T-10 的硬终止、各派发/回包/提交边界崩溃与未知写入保锁矩阵。完整 B-07 仍未关闭。
4. 到期由读/控制检查，没有独立定时通知；批准/输入与结果事实不证明真实模型会正确选择对象、使用输入或说明未知状态。
5. 无需新仓库/账号/模型 Key。下一轮在同 Local checkout 核实 Git/本页/progress，运行锁定检查，再实施独立合成事项写入/查询服务 → unknown/reconciliation → verify/respond → recover/启动扫描，补全五个 S-03 路径后才关闭 T-09。

用户资源与会话建议唯一维护在 [progress](../planning/progress.md)。

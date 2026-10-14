# T-09 外部未知效果、核查与显式恢复验证

- 日期：2026-10-14（Asia/Shanghai），开发版本 0.1.0a0
- 起点：`/Users/yanlong/projects/drov`，main / 7809192，工作区干净且与 origin/main 同步；首批 T-09 的 10 个远程 CI job 全部成功
- 前置复跑：首批 448 项中 446 passed / 2 skipped，Ruff/严格类型/schema 通过；按 progress 完成 T-09 剩余范围，未开始 T-10
- 环境：macOS arm64、uv 0.13.0；Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0
- 契约：API/运行 v3 + [ADR-0015](../decisions/0015-external-effects-recovery.md)；新增两个 verification 事件类型并生成 schema，ToolOperation 可缺省核实字段；无新依赖/uv.lock/DDL/品牌/历史 P4 指纹变化
- 状态：本轮功能/本地测试与干净安装已验收，远程状态在下文补记；真实模型、硬崩溃完整专项、沙箱、HTTP、维护/旧备份恢复均不由此证明

## 本轮证据与整卡映射

新增 [test_reconciliation.py](../../tests/test_reconciliation.py) **36 项**、[test_recovery.py](../../tests/test_recovery.py) **12 项**，共 **48 项**。撤去四个旧“不支持 write/unknown”的参数用例，保留 schema-only 接收前拒绝、deny/ask、参数/身份及计数断言；改为有真实写入与核查证据的测试。Runtime 构造仍无副作用，verify/recover 已有实际实现。全库 **492 项**；T-09 两批合计新增 96 项，第一批见 [历史记录](t09-interactions.md)。

| 验收 / 关联 | 实际证据 |
| --- | --- |
| S-03-A、B-01/04 | 独立进程非幂等事项服务计数：审批前 0，批准 1，拒绝 0；两后端并发 20 次重复批准不多写；临时入口 ASK 仍无 request_id |
| S-03-B、B-07 | 每个示例阶段新 SDK 进程重建同 Agent/依赖并响应原审批；扫描不自动执行；104 条跨 tenant/user 未结束记录分页转换；缺 revision/容量不推进；engine/response 的错误版本及 bool 标签四组均拒绝、原恢复状态保持；recover 不绕过原请求、不复活终态 |
| S-03-C、C-08 | 首批参数/身份/tenant/配置绑定及撤权测试保留；本轮 query 原用户权限、错误控制者、async verifier/版本指纹验证；not_applied 清除旧批准，使用同 operation/new request/新 attempt，次数不回退 |
| S-03-D、B-05 | 服务真实写成功后关闭连接；工具记录 unknown/暂停，verify 按原 ID 取得独立证据；正常继续需正确结果，不再执行原写入；取消时无可用输出也能凭效果证据收尾，写入保持 1 |
| S-03-E、C-09 | 五组示例的 input 路径：HumanInput 原 schema 回答 → 固定写入候选 → 新审批 → 实际修改；首批非法回答修正/注入/权限与计数断言保持 |
| 不盲重试 | write/unknown 的回包丢失、非法结果、写成功后 TransientToolError 三组，两后端写次数 1；read 原有瞬态重试回归保留 |
| 核查响应原子性 | unresolved 同响应并发 20 次只一回执/一个新请求；旧响应可重试；提醒期限到达仍能核查；正常 applied 缺结果/错类型/空证据不消费；容量/最终 save_step 已知回滚不消费、不虚假成功 |
| 取消与预算 | applied/not_applied 取消收尾均无新模型/写入；预算耗尽仍可 verify 并仅凭效果证据 failed/limit_exceeded；cancel_requested 和原已发生事实保留 |
| 查询期限/所有权 | 非协作 verifier 超时 unresolved，实际 Task/槽位/SQLite 锁保留；同 operation 第二次查询拒绝，关闭 ShutdownIncomplete，释放测试屏障后关闭成功；查询错误不展示私有异常文本 |
| 迟到建议/终态审计 | 查询期间其他合法证据消费并 completed，迟到 verify 抛 InteractionConflict，不能再推进旧请求；开始/结束不可变独立审计均保存，终态不修改 |
| 原调用仍活动 | 写 Task 拒绝取消，unknown 后 not_applied 不允许重派发；原 count 1、槽位 1，取消/释放屏障后资源清理 |
| 派发/结果恢复边界 | 在已提交 dispatched、succeeded 后注入 PersistenceUnavailable 再重开：前者保守核查/新审批后首次真实写，后者使用原成功结果；独立写次数最终 1，不把派发标记等同实际执行 |
| 最终响应恢复 | 完整最终 ModelResponse 已提交但输出未提交时重新打开，recover 无新增模型调用，复用输出并校验原 codec |
| 未提交模型响应 | 按原 ModelRequest/call ID 重试，新的 attempt、模型 step 不重复计数；此前未提交工具计划写计数 0；保守活动预算耗尽不再调用模型 |
| SDK/stream 实例 | [operations_offline.py](../../examples/operations_offline.py) 五组各一个独立服务，3/3/4/4/4 共 18 个新 SDK 进程，普通/stream 均通过；批准/输入/丢回包/取消写次数各 1，拒绝 0；重复原回执时模型 0 |

最终本地 Python 3.11：**490 passed / 2 skipped**（Python 3.12+ 即时任务工厂）；Python 3.14：**492 passed**。静态/格式/严格 mypy（72 source files）/schema 检查通过。首批 48 项交互、56 项 Memory 及旧存储/线程/进程测试保留。

早期发现并修复：SQLite kind CHECK 不接受新增审计类别，改为已有不可变 receipt 的带标签子记录，未改变 DDL；重开时查询旧事实应允许返回已保存操作，不能误当新派发；测试错误地把默认元数据摘要当正文请求、两个测试 lambda 缺类型标注、示例变量/格式和类型问题。补查最终响应 envelope 的 bool 标签原会被当版本 1 接受，四组先复现再修正，并对输入/交互/回执做同样的精确整数检查；新增四项全部通过。中间失败不计最终通过，也不把语法/脚本替身作为真实模型质量。

## 本轮命令与构件

```sh
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python examples/operations_offline.py
.drov/tooling/bin/uv run --locked python examples/operations_offline.py --stream
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
git diff --check
```

两解释器均完成 wheel/sdist、从 sdist 重建并比较 payload、各两个干净核心安装/兼容/隔离导入及五个示例普通/stream，共四次干净安装。operations 每入口各 18 个独立 SDK 进程和五个独立服务均按独立计数通过；源与服务已加入 sdist 检查。日志在 .drov/t09-full-package-py311.log / t09-full-package-py314.log，构件在 dist/ 与 .drov/dist-py314/，Twine 四个构件通过、未上传。文档 47 文件、382 本地链接、14 Python 片段 AST 及 Git 差异检查通过，保护文件无变更、差异凭据模式计数 0，结果 .drov/t09-full-doc-check.json；不读取环境/真实凭据，模式检查不证明找到所有秘密，AST 不等同 SDK 运行。随后源文件修正过期 docstring/恢复提示，并补查严格版本拒绝（见下文），远程 CI 对最终提交重新构建；不使用旧 CI 代表新代码。

## 完成界限与下一步

- T-09 五个 S-03 离线路径及 B-01/04/05/07 的功能证据已形成；本轮派发/结果边界是可控异常注入与正常进程重开，**T-10 的真实 SDK 硬终止、全部提交/回包/取消完成竞争及未知用量矩阵尚未实施**。
- 事项服务刻意非幂等、串行查询有独立台账，是自有临时夹具，不是生产服务。Drov 不保证所有外部 exactly-once，也不证明所有 verifier/人工证据真实；普通工具与只读回调须由宿主正确实现。
- async-only verifier，无后台轮询、通用外部幂等/重试适配或远程工具沙箱。未停止 Python 调用保留所有权，不保证强杀；原模型响应未提交的恢复可能产生不同输出/额外用量。
- 旧备份回退/数据清理/restore barrier/迁移仍由 T-15 实施，不能从正常 SQLite 重开推断旧备份安全。真实模型与付费 smoke 仍待 T-11/T-16；沙箱后端/镜像仍待 T-10A/T-10B，无公共空壳。
- 下一轮核对本轮提交与最新验证，在当前 Local 仓库复用独立事项服务与屏障实施 T-10；具体资源、状态和 New Chat 建议以 [progress](../planning/progress.md) 为准。

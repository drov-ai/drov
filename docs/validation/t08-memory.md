# T-08 内置事务 Memory 验证

- 日期：2026-10-13（Asia/Shanghai），开发版本 0.1.0a0
- 起点：`/Users/yanlong/projects/drov`，main / adc9793，工作区干净、比 origin/main 多一个 T-06/07 提交；先重跑 343 passed / 2 skipped 核实 T-07 前置完整
- 契约：API/运行 v3 + [ADR-0012](../decisions/0012-transactional-memory.md)；无新依赖、公共 DTO/schema 或 SQLite DDL 变化，uv.lock、品牌及历史 P4 fingerprint 保留
- 环境：macOS arm64，uv 0.13.0；Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0
- 状态：T-08 任务卡本地验收完成；远程同步/CI 状态见下文与 progress；无真实模型、模型 Key、私有数据、付费调用、包上传或部署

## 验收证据

新增 [test_memory.py](../../tests/test_memory.py) **56 项**，移除旧 T-06 “Memory 必须接收前拒绝”一个已过期参数用例；全库 **400 项**。其余外部 write/unknown/schema-only 拒绝、审批/恢复不得存在空壳的断言保留。

| 要求 / 关联 | 实际证据 |
| --- | --- |
| remember/recall/forget / C-06、B-09 | 内存与 SQLite 真正保存/读取/删除/重建：version 1 → 2 墓碑 → 3；返回 key/version 或完整 scoped 记录，memory.changed 与 tool.succeeded 的 state_version 相同，无 tool.dispatched |
| 原子失败 / B-03、S-02-D | 两后端各在修改后、成功操作记录后、事件/检查点后、已知回滚的 commit 边界注入失败；无 Memory、成功结果/事件或工具预算增长，模型只调用首个计划步骤；PersistenceUnavailable 留最后已提交事实 |
| 条件更新与去重 | 两会话同 key/version 并发，一次 succeeded、一次 memory_conflict；重复 operation 返回原结果，异参同 ID 抛 Conflict，版本不再增长，写事件只一次 |
| 作用域 / B-10、S-02-C | 自动及显式 recall 分别验证 tenant/user/Agent 隔离；存储查询先带三项归属，再过滤；七进程示例逐方向验证无法读到 Alice 偏好 |
| 最近/键/子串 | 两后端 110 条同时间戳记录 + 新记录/墓碑/另一用户；updated_at 降序、key 升序稳定；匹配跨 100 行页，NFKC 全角/casefold Straße；最近和子串无墓碑，精确 key 可读删除元数据 |
| 期限 / B-08、S-02-B | required/best_effort × 超时/异常/取消后返回迟到值；required 模型调用/step 为 0、rejected 清单状态 timeout/failed；best_effort 本次不用迟到值，下一步骤可重新读取 |
| 清理与所有权 | 取消后正常读取清理完成；故意拒绝停止超过 100ms 的读取抛 PersistenceUnavailable、模型 0，保留真实 Task/槽位，释放屏障后才释放；阻塞读取返回已超过截止时间也不使用 |
| 删除/快照边界 | 检索后、输入提交前更新/删除：旧版本以 memory_version_changed/memory_deleted 排除，更新版或当前 empty 同事务保存；输入提交后删除，重试仍用相同调用 ID/输入/hash，后续新会话不读已删除值 |
| 份额与协议粗估 | memory_fraction、memory_max_tokens、保护输入余量各约束；完整记忆消息与 JSON/协议开销计入份额，超大整条排除继续尝试小条；全请求 estimate 等于实际保存请求，无保护输入丢失 |
| 显式 recall 来源 / C-07 | 保存实际 key 来源的 opaque hash ID/版本，回填清单 explicit_recall；工具预览时来源同步标 preview 且正文 None，不暗示模型读过整条 |
| 权限/取消 | deny/ask/业务否决/等待后撤权/取消均无修改和 budget；ASK failed/interaction_unavailable；保留名不能通过 compile_tool 绕过；普通对话无隐式保存 |
| 参数/限额 | 空 key、负版本、bool 版本/limit、注入 user_id、key/query 冲突及越界 limit 拒绝；记忆字节/输出超限无写入；工具次数耗尽事务回滚，先前合法成功保留 |
| 提交后超时 | 在已完成提交后延迟返回直到 timeout：读取并保留 succeeded 与 Memory version 1，不改写成未执行或重做 |
| 正文隔离 | metadata inspect 逐事件页及 get_context 不含私有 key/content；记忆 source ID 不含明文 key；继续复用 T-06/07 的动态撤权、正文读取后二次授权和返回对象隔离回归 |
| S-02 实际示例 | [memory_offline.py](../../examples/memory_offline.py) 普通/stream 各七独立进程：保存、新会话读取、三个隔离组、删除、删除后新会话；核对 PID、实际 tool result/content/version/来源及写入事件；临时 SQLite 自动清理 |

最终 Python 3.11：**398 passed / 2 skipped**（Python 3.12+ 即时任务工厂 API）；Python 3.14：**400 passed**。Ruff、格式、严格 mypy（61 source files）、公共/存储 schema 漂移检查通过。

早期新文件的格式/类型检查及 S-02 例子类型注解有失败，修正后才运行最终验证；T-06 旧拒绝 Memory 断言按已实现范围移除。补查发现显式 recall 预览还携带完整来源正文，以及完成但已超过截止点的读取可能被接受；均同轮修正并增加独立边界断言。早期结果不作为最终成功证据。

## 实际检查命令与构件

```sh
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python examples/memory_offline.py
.drov/tooling/bin/uv run --locked python examples/memory_offline.py --stream
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
git diff --check
```

两解释器分别完成 wheel/sdist 构建、从 sdist 重建并比较 wheel payload；各在两个干净核心环境安装/兼容/隔离导入检查，再从仓库外运行检索、预览及 Memory 三个示例的普通/stream 入口。共四个干净安装，Memory 各运行七个独立子进程。构件与 package-check.json 保存在 dist/ 和 .drov/dist-py314/，未上传。文档相对链接、Python 片段语法、状态/引用及变更范围凭据模式计数检查保存到 `.drov/t08-doc-check.json`；模式检查不证明任意秘密均被识别，环境文件/真实密钥不读取。

## 远程 CI 与跨平台修正

T-08 首次实现提交 [d997f17](https://github.com/drov-ai/drov/commit/d997f17f7bcf34f6ceea170b378fab956240923b) 已推送 main，包含原本只在本地的 T-06/07 提交。首轮 [CI 38412265349](https://github.com/drov-ai/drov/actions/runs/38412265349) 为 failure：quality、Linux 四版本、macOS 两版本和 Python 3.15 advisory 通过；Windows 3.11 的旧线程测试在已释放线程后的第二次 aclose 仍要求 20ms 内完成 SQLite 清理，Windows 3.14 的安装验证因中文示例重定向到 cp1252 抛 UnicodeEncodeError。两份日志都显示 T-08 的 56 项 Memory 测试通过，不能据此跳过整套 CI 的失败。

同轮修正：旧线程测试的收尾在 5s 外层期限内重试符合契约的 ShutdownIncomplete，最初的线程仍占槽位/SQLite 锁断言保留；安装验证的示例解释器显式传 -X utf8，保留 -I/-B 和核心隔离。没有修改 SDK 超时/所有权语义、全局编码或代理。修正后本地两解释器各重跑 41 项工具回归通过，静态/类型/schema 通过，并重跑各两套干净安装/三个示例普通与 stream 验证；第二轮 [CI 38412955005](https://github.com/drov-ai/drov/actions/runs/38412955005) 的 Windows 3.14 已完成测试/构建/安装/extras，但 Windows 3.11 另一个 T-04 线程所有权测试暴露同样的 20ms 收尾假设。审查同类测试后，仅在已释放测试屏障、已完成最初失败/锁/容量断言的收尾使用公共测试夹具 close_released_runtime，在 5s 内重试既有 aclose；最初所有权与错误断言全部保留，SDK 没有改动。同轮修正后再次全库回归：本地 Python 3.11 为 398 passed / 2 skipped，Python 3.14 为 400 passed，静态/格式/严格 mypy/schema 与文档检查通过。最终修正提交 [5ff654c](https://github.com/drov-ai/drov/commit/5ff654cf0ac8044125554029acae0638bc85ca6a) 的 [CI 38413817284](https://github.com/drov-ai/drov/actions/runs/38413817284) 共 10 个 job 全部 success：quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory；八个 package job 的测试/构建/两个核心干净安装及三个离线示例普通/stream、extras 隔离导入均通过。Python 3.15 仅探测，不升级支持承诺。完整 job/摘要保存到 .drov/t08-ci-status.json 与 .drov/t08-ci-summary.log。最终补记只改变 progress/验证文档，使用 skip ci 避免重复代码矩阵，实际 HEAD 以 git log 为准。

最终 CI 实际测试计数（没有新增跳过）：

| 平台/解释器 | 结果 |
| --- | --- |
| Linux / macOS 3.11 | 各 398 passed / 2 skipped：即时任务工厂仅 Python 3.12+ |
| Linux 3.12/3.13/3.14、macOS 3.14 | 各 400 passed |
| Windows 3.11 | 397 passed / 3 skipped：上述两项及打开 SQLite 文件替换不适用 |
| Windows 3.14 | 399 passed / 1 skipped：打开 SQLite 文件替换不适用 |
| Python 3.15 advisory | 400 passed，仅作为探测 |

## 未实现、未验证与恢复步骤

- T-08 卡无剩余本地验收项；内置记忆实际写入/读取是存储事实，预设回答不证明真实模型会选对保存工具或正确使用偏好。实际质量待 T-11/T-16。
- 只有 scoped 最近、精确 key 和完整子串；没有语义/向量检索、自动提取或外部 Memory。扫描/清单规模随记录量增长，未验收大规模检索性能；估算不是精确 token、费用或硬上界。
- forget 不删除旧历史/已提交输入/Trace/备份；重试固定旧快照，后续新检索排除墓碑。完整清理/迁移仍待 T-15。
- 所有 ASK 当前失败；外部 Python write/unknown、HumanInput、审批响应、verify、recover/resume 仍待 T-09/T-10，无空壳 API。
- 非协作后端清理失败保留所有权并抛 PersistenceUnavailable，可能保留运行中记录；此时模型未调用，不伪造 durable 终态；显式恢复待 T-09。内置存储提交若不确定继续沿 T-03 的 poison/锁规则处理，不据本轮已知回滚注入声称覆盖全部进程崩溃。
- 后续在当前项目 Local / `/Users/yanlong/projects/drov` 核实 Git/progress/本记录，运行锁定检查，按 T-09 卡实施审批响应 → HumanInput → 核查/显式恢复。T-09 无需新账号或 Key；T-11/T-16 真服务前再明确服务、model ID、总预算及本地安全配置，聊天不贴凭据。

用户准备/任务状态/会话切换唯一维护在 [progress](../planning/progress.md)。

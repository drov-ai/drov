# T-06 只读离线 Agent 闭环验证

- 日期：2026-10-13（Asia/Shanghai），开发版本 0.1.0a0
- 起点：`/Users/yanlong/projects/drov`，main / 0b2ac9b，工作区干净；先核对 T-05 完整本地/CI 验收，再重跑原 257 项测试：256 passed / 1 skipped
- 契约：API/运行 v3 + [ADR-0010](../decisions/0010-read-only-agent-loop.md)；无新依赖，uv.lock、历史基线和品牌源文件不改
- 环境：macOS arm64，Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0；uv 0.13.0
- 状态：T-06 任务卡本地验收完成；未推送本轮变更，未执行新远程 CI，未调用真实模型或发布部署

## 验收结果

新增 [test_agent_runs.py](../../tests/test_agent_runs.py) 共 53 项，包括普通/流式参考例子；全库收集 310 项。

| T-06 要求 / 关联 | 实际证据 |
| --- | --- |
| 只读闭环 / C-01/02/04、S-01-A | 真实同步/异步检索工具、完整模型计划先提交、下一请求含匹配工具结果；dataclass Report 严格解码，引用集合等于实际合成资料来源；内存与真实 SQLite 均通过 |
| 接收与容量 / B-02 | 同键并发 20 次一个 Run；同键不同输入冲突；满容量/SessionBusy 不阻止同键重试；容量不足无新 Session；完成后同键仍返回接收时 RunRef |
| 接收所有权 / B-06/12 | 接收提交屏障期间取消调用者，事务/调度仍由 Runtime 持有；关闭宽限失败保留资源，提交后无法调度保存 suspended/recovery；调度故障不遗留无人推进的 accepted |
| 配置拒绝 / C-12、B-01 | schema-only、write/unknown、Memory 在接收前拒绝，无模型调用/新会话；临时 ASK failed/interaction_unavailable，工具计数 0，操作 not_executed，无 request_id |
| 临时/同步 / B-13 | 临时身份不修改原 Agent、借用模型不关闭；应用限额可显式覆盖；已有事件循环时 run_sync 明确要求 await，模型计数 0 |
| 返回与流 / C-10、B-11 | durable seq 连续、最终事件输出与结果一致；分页/同 Run 游标补读一致；旧 suspended 事件不截断当前 running；追平终态/恢复暂停后结束 |
| 缓冲 / B-11 部分 | 缓冲=2、增量上限=3 bytes，慢消费与超大增量合计 7 条丢弃并发 gap；durable 事件完整，元数据订阅不带 token |
| 等待/流断开 / B-06 | 取消 wait 或退出 stream 不置 cancel_requested，执行及槽位仍在；随后可取得同一完成结果 |
| 正文权限 / B-10 | run/stream 缺 view_content 接收前拒绝；只有元数据权限可 start/inspect/events，输入/输出不进入元数据；缓存事件发送及最终 wait 撤权拒绝，合法执行不回滚；Bob 的同 ID 查询不可见 |
| 协议原始项 | provider_items 下一模型请求正确往返，正文 inspection 不包含；工具参数/实际结果在授权后的事件中可见 |
| 模型失败/重试 | TransientModelError 独立 attempt_id、固定保存输入/model_call_id；普通异常/超时不重试；缺 complete、complete 后增量等协议错误计数工具 0；异常正文不暴露 |
| 输出 / B-15 部分 | 数字强转、重复 JSON 键、NaN、多余字段、超大结果明确 invalid_output；绝对过期/保护请求超预算时模型计数 0 |
| 预算事实 | max_tool_calls=1 两工具只派发一次，第二操作终态保存 not_executed；max_model_steps=1 不再请求第二步；请求不能放宽管理员限额；已知用量累加、未知不填 0，非法输出保留已知消耗 |
| 历史 / B-02/15 部分 | 失败输入及下一输入各一次，不完整流不入历史；取消计划为全部 call_id 留明确结果；冻结相同时间戳且逆字典序 Run ID，当前输入仍最后 |
| SQLite 重开 | 原去重引用、失败结果、模型步/attempt/工具派发计数和活动时长事实完全保留，无额外模型请求；不是显式 recover 工作流验证 |
| 取消/终态竞争 | 显式 cancel 有持久事件，终态取消幂等；已完成结果/版本不被取消改写；真实线程的迟到结果不变成成功 |
| 未停止调用 / B-12 | 真实线程取消后仍占槽位，SQLite Runtime 关闭报 ShutdownIncomplete；放行后才关闭/重开；不合作模型 Task 超时后同样持有槽位直至实际结束 |
| 提交故障 / B-03 部分 | accepted 提交失败无记录/槽位/调用；模型计划提交或 tool.dispatched 提交失败工具计数 0；最终提交失败不伪造 completed/failed，调用方收到 PersistenceUnavailable |
| 即时任务工厂 | Python 3.14 的 eager_task_factory 验证接收/模型 Task 先归属再进入用户代码；3.11 API 不适用 |

Python 3.11：**308 passed / 2 skipped**，两个跳过均为 Python 3.12+ 即时任务工厂 API（原 T-04 与新增 T-06）。Python 3.14：**310 passed**。静态 Ruff、格式、严格 mypy（含 examples）、公共/存储 schema 漂移检查均通过。

## 实际检查命令

```sh
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python examples/research_offline.py
.drov/tooling/bin/uv run --locked python examples/research_offline.py --stream
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
```

两解释器各构建 wheel/sdist、从 sdist 重建相同 wheel payload，分别在两个干净核心环境中安装/检查兼容依赖、验证隔离导入，并从仓库外运行实际安装包的普通/流式检索示例。不使用源码路径替代安装包导入。sdist 新增 examples，wheel 仍仅含 drov 和分发元数据。构件只保存本地，不上传。

文档相对链接、Python 片段语法、能力/案例引用、任务依赖、基线/锁/品牌未变及可识别凭据模式检查另记本地 `.drov/t06-doc-check.json`。模式检查只报告数量，不输出潜在凭据；它不是任意内容安全证明。

## 限制和恢复步骤

- T-06 全任务卡已完成；T-07 的组选取/裁剪、预览和 ContextManifest 尚未实现，inspect 清单为空、get_context 无空壳入口。长历史当前 context_overflow，不能宣称已具备完整可检查上下文。
- 查询只支持 context 的 tenant/user 作用域；跨归属审批/管理员查看、HTTP/SSE、跨平台 CI、真实模型质量仍未验证。所有 ASK 当前失败，Memory/write/unknown 不接收。
- T-09 才实现启动扫描和显式 recover/respond/resume；存储故障后保持最后成功提交事实，修复资源后由后续显式恢复规则处理。本轮不把 SQLite 重开或 dedup 当作完整 Agent 恢复。
- 活动时长预留是保守事实，不能证明实际调用已结束；未停止调用继续占槽位/锁，关闭失败后保持原 Runtime，待实际停止再重试 aclose。线程不能由取消信号强制终止。
- ScriptedModel 与合成资料只证明契约；真实服务/model ID/本地凭据/预算在 T-11/T-16 前准备，未授权费用前不调用。

下一项为 [T-07](../planning/implementation-plan.md)；进度与会话切换建议唯一维护在 [progress](../planning/progress.md)。

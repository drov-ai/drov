# T-10A 第二批：Runtime 文件输入与授权读取

日期：2026-10-14（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`、main / 412e39b，干净；保留首批 f2f45c1。读取 AGENTS/progress/导航/handoff、首批验证/ADR-0017、API/运行/工程/任务。前置复跑 3.11 **649 passed / 2 skipped，76.66s**，确认首批已验收而整卡尚未完成。

## 实际子范围

| 对象 | 证据与限制 |
| --- | --- |
| Runtime 装配 | owned/borrowed/factory 或显式 LocalArtifacts；SQLite 配对/排他租约/逆序自有关闭，借用已打开且配同一 SQLite 实例，不关闭借用资源；无 store/内存/临时文件配置接收前拒绝 |
| 接收/去重 | start/run/stream 有序输入/hash/类型规范摘要，空清单不增摘要键；lookup 先于新字节/容量；重复请求同 Run、不增阶段/模型调用，内容/名/类型/顺序冲突明确拒绝 |
| ready 输入与授予 | 字节先可靠预留/暂存/校验；SQLite 同事务保存输入 ready、原来源、Run/配置/授予/去重/检查点/接收事件；原引用显式再次授予，不复制/转移原字节，文字 ID 不授予 |
| 权限/字节 | ArtifactResource 原归属/Agent/来源与目标 Run/Agent/session，tenant 不可覆盖、默认本人且不跨 Agent 复用；显式同 tenant 共享；读前后 view_content 复核，读取中撤权不返回字节，默认元数据无名/路径/字节 |
| 出错事实 | 真 SQLite trigger 拒绝 grant：Ready/Run/去重/授予全回滚，私有阶段保留而不可查询；接收调用方取消控制 Task 仍完成一次，重试复用；损坏/缺失/链接 ready 字节拒绝并提交 unavailable，不重算 |
| 上下文 | 当前获准描述进入保护预算/来源，不含字节/宿主路径；ContextManifest 有文件 v2、无文件 v1；新版兼容 v1/v2，v1 不得包含 artifact，boolean/未知版本拒绝；模型调用前复核授权/hash |
| 可运行示例 | [artifact_inputs_offline.py](../../examples/artifact_inputs_offline.py) 普通/stream：实际 SDK 接收、授权清单、关闭后新 Runtime 读 15 bytes CSV，hash=`62f26beb787b9830f07f5a3608df5e0bff80589b4fa3ba85c22d8f6c7daf1301`；ScriptedModel 仅描述元数据，未读 CSV/执行分析/导出 PNG |

新增 [test_artifact_inputs.py](../../tests/test_artifact_inputs.py) **34 项**，首批 38 项保持。接收后输入真实删除会 durable failed/artifact_unavailable，模型调用 0；caught Artifact SQL 写错误使整个事务失败，不允许部分 ready 留存。最终 3.11：683 passed / 2 skipped（84.69s），3.14：685 passed（85.46s）；Ruff/格式、mypy 87 文件/schema、两解释器 wheel/sdist/重建、四套干净核心安装及六示例普通/stream、Twine strict 通过。首次 3.14 为 684 passed / 1 failed，原因是新原生 sqlite3 测试连接未显式 close 的延迟 ResourceWarning；改用 closing 后全库复跑通过，未归咎旧 history 实现。

## 数据与兼容

[ADR-0018](../decisions/0018-artifact-input-admission.md) 新增 Artifact SQL 扩展 v1，由显式 LocalArtifacts 配对事务启用；普通核心库仍两张表。records DDL/user_version/record schema=1 不变，新版读旧数据；旧 SDK 会拒绝带扩展的新库，不能声称可混用写入。Ref/Info/Grant v1，ContextManifest 的新 artifact 枚举明确 v2，无文件 v1。完整 DDL/新增 ArtifactResource schema 已生成；未增依赖或改 uv.lock/品牌/P4 指纹。

Artifact staged/ready 是不同事实。接收失败私有字节仍占容量，没有自动回收/恢复入口；不得删残留/重写已开始的阶段后声称故障验收。原来源与 Run 授予在两个扩展表中，字节在配对私有目录；只复制 SQLite 或只复制 blob 不等于完整备份，T-15 仍未验收。

## 本轮验证方法

```sh
.drov/tooling/bin/uv run --offline --locked pytest -q
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --offline --locked \
  --python /opt/homebrew/bin/python3 pytest -q
.drov/tooling/bin/uv run --offline --locked ruff check .
.drov/tooling/bin/uv run --offline --locked ruff format --check .
.drov/tooling/bin/uv run --offline --locked mypy
.drov/tooling/bin/uv run --offline --locked python scripts/generate_schemas.py --check
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --out-dir .drov/t10a-input-dist-py311
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 \
  --out-dir .drov/t10a-input-dist-py314
```

测试中实际发现并修正：内部 SessionScope 克隆不能保留 session_id 送入仅接受 tenant/user 的 RunContext；模型执行权限原本因此被错误否决，修正后正常接收/描述/读取。新来源枚举依维护规则使用 ContextManifest v2，保留 v1 并拒绝 boolean；不是只改 enum 而沿用 v1。接收后文件丢失与 SQLite 不可用区分，前者提交明确 Failed，避免留下失去 owner 的 running。ArtifactInfo 作为 Ref 子类提取四个引用字段再验证，既支持固定引用，又不让 name/state 等客户端字段改变元数据。静态发现及测试夹具字段错误已修正后复跑。

日志 `.drov/t10a-input-{baseline,final-py311,final-py314,package-py311,package-py314}.log` 等是本 checkout 忽略文件。复核当前 Local Docker 分析 image ID 存在、Linux ARM64；未创建新执行容器、运行任意模型代码或从旧可信探针推断本批 SDK 沙箱成绩。

## 未完成与下一批

**只验收文件输入/读取子范围，T-10A 整卡不关闭。** PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult、真实后端预检、配置/代码/文件审批、稳定执行键/预算/持久租约、受保护容器输入、安全导出/PNG 校验、销毁前暂存与销毁后 output ready/原工具结果共同提交均未实施。没有模型生成代码/完整 CSV→Python→CSV/PNG→同一 Agent 循环验收。

下一批先做标准库真实 PythonSandbox 审批/执行与租约，再接受保护输入/安全输出/分析镜像闭环，保持同一 T-10A。最终闭环需要审批前创建 0、已派发不重跑、两次不隐式共享、非法/超限无悬空引用、销毁后授权 hash 下载及安装证据；整卡完成才进入 T-10B/T-11。T-10C 未建模块，不需要云账号/Key。当前保持 Docker Desktop 开启，无新账号/模型 ID/预算；远程网络条件、下一轮会话建议与 Git 状态统一在 progress。

文档检查：58 个 Markdown / 469 个相对链接 / 14 个 Python AST 片段，错误 0；这不是 SDK 运行验证。构件/安装在最终夹具关闭修正和文档同步后再次生成、验证，日志已保存。

## Git / 远程

原 f2f45c1/412e39b 与本批 **29d225d** 已成功推送 origin/main；37 个跟踪文件经保护/敏感模式与差异检查，无 uv.lock/品牌/P4/模型凭据或 `.drov/` 数据变更。首轮核心 [CI 38532234182](https://github.com/drov-ai/drov/actions/runs/38532234182) 有下述 Windows 失败；最终第三轮已完整通过。不代表尚未实现的 Docker 专项。

首轮远程 29d225d：Windows 3.11 为 607 passed / 75 skipped / 3 failed（641.85s），plan-after-stream 的服务计数为 0，两个 kill 非协作夹具未发 live_call 而等待超时；其他平台结果保留 CI JSON。普通 fixture 的 model/tool 期限原 2s，脚本 final 接受工具错误的路径仍可能 completed；本轮将普通期限改 10s，专项 attempt/time 预算仍 2s、非协作 tool=30s/shutdown=0.2s 不改，并加入未进入实际调用的即时 outcome 诊断和断言上下文。SDK 默认/规则与计数、锁、硬终止断言均不放宽。本地对应 97 项矩阵复跑通过；当时 Windows 修正尚待远端验证，后续第二/三轮结果保留如下，不把首轮失败当成功。

第二轮 ef1b0b4 / [CI 38534476974](https://github.com/drov-ai/drov/actions/runs/38534476974)：Windows 3.11 全流程 success；Windows 3.14 两个 SDK 部分成功屏障超时，610 passed / 73 skipped / 2 failed（641.74s），其他 8 job success。异常发生在父进程的启动到屏障 20s 等待，未拿到结果事实，不能据此判定 SDK 重做或错误成功。控制进程等待包括解释器启动、SQLite fsync 和多次审批，与模型/工具期限分开，现调整为有界 45s；子进程超过 15s 在 stderr 输出实际 Task 栈，父进程超时终止并收集 stdout/stderr，避免丢失诊断。SDK 默认、model/tool 10s 和专项预算 2s 不变；服务次数、原计划两调用/锁/硬终止断言不改、不跳过。本地 97 项再次通过（51.81s），最新夹具的 sdist/干净安装复核通过，wheel hash 不变；第三轮实际结果如下。

最终夹具修正 **e83d566** 已推送；[CI 38536288876](https://github.com/drov-ai/drov/actions/runs/38536288876) 共 **10 个 job 全部 success**，日志/JSON 保存在 `.drov/t10a-input-ci-third.*`。八个 package job 的 wheel/sdist/重建、核心干净安装、适用离线示例普通/stream、Twine、extras 隔离导入全部通过；未调用真实模型服务或 Docker backend。

| 平台 / Python | 实际 pytest 结果 | 时间 |
| --- | --- | --- |
| Linux 3.11 | 683 passed / 2 skipped | 165.49s |
| Linux 3.12 | 685 passed | 172.55s |
| Linux 3.13 | 685 passed | 156.71s |
| Linux 3.14 | 685 passed | 167.49s |
| macOS 3.11 | 683 passed / 2 skipped | 196.53s |
| macOS 3.14 | 685 passed | 225.14s |
| Windows 3.11 | 610 passed / 75 skipped | 404.79s |
| Windows 3.14 | 612 passed / 73 skipped | 482.76s |
| Python 3.15 advisory / Linux | 685 passed | 165.34s |
| quality | Ruff / format / mypy / schema success | 非 pytest |

Windows 明确跳过 72 项 POSIX Artifact 字节/输入验证、1 项 Windows 不允许替换已打开 SQLite 的验证；3.11 另有 2 项新即时任务 API 不适用。Windows 通用 Runtime/存储/崩溃/安装矩阵保留运行，文件配置在 Windows 预先拒绝，不能称为 Windows 文件能力验收。远端通过支持本次测试夹具修正，不构成启动到屏障 20s 的性能保证；专项预算、状态事实和恢复不得重做断言未放宽。

最终进度/验证补记只修改文档，链接/AST/差异与保护检查后用 skip ci 提交同步，不重跑不变代码。最终 Git 以实际 log/status 为准；无未处理的核心 CI 失败。当前只关闭第二批子范围，T-10A 整卡、Docker 专项和真实模型质量均不从这份报告推断完成。

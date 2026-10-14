# T-10A：Runtime Python、文件导出与持久产物闭环

日期：2026-10-14（Asia/Shanghai）。目录 `/Users/yanlong/projects/drov`，Local / main。接续前批 0780093 的 Docker 控制/预检检查点；用户明确要求继续未完成部分。本轮前已读取 AGENTS、progress、规划导航/handoff、实际 Git/源码/验证，T-10 无缺项，未重开 P4。保护品牌、uv.lock、历史 P4 fingerprint、旧验证记录和 core records DDL。

## 实际交付与验收分界

按 [ADR-0020](../decisions/0020-runtime-sandbox-execution.md) 实现公共 PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult、Runtime owned/borrowed/factory 装配及固定 backend hash、专用 write/default ask 派发、原审批/code/输入/输出清单、同事务执行租约与预算、稳定 container/exec 身份与单次执行。临时 Agent.run、无 SQLite/配套 LocalArtifacts/固定后端提前拒绝。普通安装/导入不连接 Docker。

容器内不同非 root UID 分隔可信控制和代码；输入位置保持 `/work/inputs/<id>/<name>`，受保护祖先/只读文件；有界真实 stdout/stderr/退出，声明集合安全导出与 PNG 解码 profile。原观察/字节可靠暂存，确认整个容器销毁后同事务发布 ready、原工具结果/event/checkpoint 与释放 lease。SQLite 显式 sandbox extension v1 不改旧 records/user_version；旧 SDK 拒绝扩展库，新 SDK 读旧普通库。verify 只读，recover 查/清原资源并复用原阶段；人工 reconciliation response 不能伪造清理。

**T-10A 本地实际闭环、安装及最终修正远程核心 CI 已全部通过，整卡验收完成；下一项 T-10B。** 不提前宣称 T-10B、Linux Docker 专项 CI、全部恶意隔离/资源/硬终止边界、HTTP/UI、维护备份、E2B 或真实模型质量通过。

## 环境与来源

- 核心：CPython 3.11 / 3.14、uv 0.13.0，实际路径见包检查日志；无依赖/uv.lock 升级。
- 本地 Docker：Docker Desktop 的 Linux ARM64 VM，CLI/Engine 27.0.3，本地 Unix socket；cgroup v2。默认 seccomp=unconfined，SDK 显式固定 Moby v27.0.3 profile 并核对实际生效，不改 daemon 设置。
- 标准库镜像 manifest：`docker.io/library/python@sha256:dabc8c628ee25179a9fcb8ab81e592938ee2bad3ffb15e64157f8fe428d5279f`；本机 config ID `sha256:3c50954f1f466ff97efcbe7678f38adf0401eb3ddda4966e04f5be9cd6c71aed`。
- 本地分析镜像 config ID：`sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45`，RepoDigests 为空，不能冒充 registry manifest。实际 wheel/许可/recipe 见 [首批记录](t10a-artifact-staging.md) 与 [sandbox](../../sandbox/README.md)。
- 固定 profile hash `9c1025c88ccaa517b648da571961838744ea2137f176bfe6a48b21294cae9c76`；归属/许可在 THIRD_PARTY，包内保留 Moby LICENSE。
- 非秘密本机快照 `.drov/sandbox-environment.json` / `.drov/t10a-analysis-image/image.json` 不提交，换机器重新核对。不会读取真实 Key、挂宿主仓库/凭据/socket，执行期不安装、拉镜像或调用云。

## 测试与真实 SDK 证据

本批新增 45 个：10 个 Unix Engine 协议/有界输出测试，13 个实际 SQLite/Runtime 审批发布恢复测试，13 个 PNG/结果/租约契约测试，9 个真实本地 Docker 用例。Runtime BackendFixture 明确是替身，仅支持持久/授权/提交语义证据；实际代码/退出/文件/清理由 Docker 组和 SDK 示例证明。

| 检查 | 实际结果与证据 |
| --- | --- |
| 包括真实 Docker 的完整 3.11 | **802 passed / 2 skipped，104.89s**；跳过仅两个 Python 3.12+ API；`.drov/t10a-runtime-confirmed-py311.log` |
| 包括真实 Docker 的完整 3.14 | **804 passed，111.45s**；`.drov/t10a-runtime-confirmed-py314.log` |
| 未提供 Docker 配置的普通核心 | 3.11：793 passed / 11 skipped，90.48s；3.14：795 passed / 9 skipped，95.91s；9 个真实组未跑不可当通过，另 2 个为 3.11 适用跳过；`.drov/t10a-sdk-final-py311.log` / `py314.log` |
| 真实 Docker 基础组独立复跑 | 9 passed，9.31s；`.drov/t10a-sdk-docker-tests2.log`；之后又纳入两套完整测试 |
| 默认审批/两次独立/显式复用 | 审批前按原 execution key 查询无容器；批准后真实一次；同 Runtime 两个不同 Session 环境无哨兵共享，仅显式授予原 ready Ref 后可读取，销毁/lease 确认 |
| 非法导出 | 缺失、symlink、hardlink、FIFO、单文件超限、畸形 PNG 实际容器样本：exit 0 保留，artifact rejected，无任何 ready 引用；代码异常真实 exit 1/stderr，32 字节 stdout 上限实际终止 |
| 实际取消 | 确認原 exec 已启动后 SDK cancel；独立销毁整个原容器，active lease 释放，无后续模型请求 |
| SQLite 拒绝发布 | 真 SQLite trigger 拒绝 output ready 插入，原 ready/result 全回滚且暂停恢复；撤去故障后 recover 发布原已保存观察/字节，BackendFixture execute 计数仍 1；不算真实硬崩溃或 Docker RPC 丢应答 |
| 人工核查/只读 verify | 未确认清理保持 unknown/active；人工 applied response 拒绝；verify 仅资源查询，无 execute/destroy；显式 recover 才确认并复用原事实 |

复跑真实组只需要以下三字段的本地非秘密 JSON：`docker_cli`、`docker_host`、`image`。目录/CLI/socket 必须是可信宿主配置，镜像必须已安装并固定。不要放凭据或给模型。

```sh
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" \
  uv run --locked pytest tests/test_sandbox_docker.py
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" \
  uv run --locked pytest
```

[sandbox_offline.py](../../examples/sandbox_offline.py) 用确定性 metadata 驱动模型提议预注册代码，实际 SDK 接收合成 CSV → ask/resume → Docker Python → CSV/PNG → 原工具结果 → 同一 Agent 最终输出。Runtime 关闭后新 Runtime 无 Agent/backend，通过授权 read_artifact/get_artifact 下载并校验 hash/PNG。普通与 stream 均通过；`.drov/t10a-sdk-analysis-passed.log`、`t10a-sdk-analysis-stream.log` 和各目录 report.json。标准库普通/stream 同样真实执行 CSV，分析模式实际 numpy/pandas/matplotlib；不是替身算结果，也不是真实模型生成质量。

| 文件 | 实际字节 / oracle / SHA-256 |
| --- | --- |
| numbers.csv 输入 | 15 bytes；value=12,18,30；`62f26beb787b9830f07f5a3608df5e0bff80589b4fa3ba85c22d8f6c7daf1301` |
| summary.csv | 25 bytes，`count,sum,mean\n3,60,20.0\n`；`c1374df62a3a24c5102ef00107866049bf9ff7d38c39a0c510ae4fa4848a7a06` |
| chart.png | 8883 bytes，PNG 解码 400×300；`8258edd76d9fca04ef58a08c3c46292906b8aaacce09a1541c7e2aebda22ec46` |

## 实际修正与限制

1. Docker cp 在只读根/tmpfs 环境无法完成本次传输，改为固定可信 stdin 安装与分开的 Engine exec 控制，不放宽根目录/挂载限制。
2. 早期物理输入位置偏离前批 `/work/inputs/<id>/<name>`，同轮修正，保留上下文 v2；实际 CSV/PNG 复跑。代码 wrapper 禁用 dumpability、控制/代码不同 UID，不信任 stdout 自述退出或清理。
3. SDK model message 的授权描述是固定前缀 JSON 文本，tool result 包含 status/result 包装；第一次示例夹具解读错误使最终回答失败，修正夹具后普通/stream 重跑。未把失败轮写成完整成功。
4. SQLite trigger 错误原先被归为普通 Conflict/stop_error；改为 PersistenceUnavailable/显式恢复，真实 rollback/原阶段测试通过。取消发布的恢复事务避免嵌套 journal transaction。
5. 导出只接受完整安全声明集合，拒绝重复 basename。PNG 为有界静态非交错 profile，允许的 ancillary 和像素/解码限制唯一维护在工程第 9 节；部分合法 PNG 也会拒绝。未增加 Pillow 或核心分析库。
6. 最后复核发现 sandbox verify 漏用了既有核查审计；改为共用原用户权限/限时/真实调用所有权/started-completed 审计，补 paired verification_id 与终态 recover 拒绝断言；recover 清理前复核权限/终态。相关 50 项核查/沙箱回归通过，最终独立解释器完整矩阵见上表。
7. 补验一批 uv run 不同解释器并发复用了 .venv，出现全局 pytest 回退/子进程环境被替换；那批 3.13/3.14 混跑失败日志不作验收。改为固定 .venv 3.11 与 .drov/check-py314 3.14 各自 python -m pytest，最终 802+2 /804 通过。末次构建遇到 PyPI TLS/EOF，使用已核对的 uv 缓存 UV_OFFLINE=true 重跑，无依赖升级/关闭 TLS。
8. Docker/cgroup/secomp 的实际配置/基础效果已复核，但目前真实验收机器只有本机 Linux ARM64 VM。Linux AMD64 分析锁/镜像、Windows Docker、完整 CPU/OOM/PID/盘风暴/路径竞态/网络哨兵/硬终止/清理失败矩阵与 Docker 专项 CI 待 T-10B。没有完整维护备份/回收或敌对公网强隔离承诺。

## 静态、构件、远程与收尾

本批 Ruff、格式（167 files）、mypy（105 source files）、生成 schema check 全部通过；62 Markdown / 510 相对链接 / 14 Python AST 错误 0（AST 仅说明片段语法）。工具名称错误信息最后改为通用 Built-in tool；相关两组 73 passed /1.14s，不改变完整矩阵的行为或数量。一次错写 tests/test_tools.py 路径未收集测试，已改为实际 test_tools_schema.py / test_tool_execution.py；未把无测试算通过。

两解释器各完成 wheel/sdist、sdist 重建逐文件比对、两套全新 core-only 安装（共四套）、依赖/import/许可与构造无 I/O 检查、六个核心离线例子 normal/stream；Twine strict 四构件通过。记录 `.drov/t10a-runtime-verified-package-py311.log` / `t10a-runtime-verified-package-py314.log` 与对应 package-check.json：wheel SHA-256 `4030293d2e4a45aee1c1be98ce54d42fd7db4c6fcb1ebb479ee3bc3f029ad454`，sdist `4b8816e639b8a1ed6747946689520e03a9d35dca964b8e316e8c19a6cbadbbd0`；两解释器一致。此构件在最终验收文档补记之前构建；不把之后的文档时间戳/报告更新写成同一 sdist，最终 wheel 所有 drov/ 文件逐字节等于提交的 src/drov，实际解释器3.11.9/3.14.3及源码路径在 .drov/t10a-runtime-interpreters.json；最后补记只改文档，不重跑已通过的矩阵。

另外两套全新 3.11/3.14 **非 editable、core-only wheel** 环境，从仓库外 -I/-B 检查真实 site-packages 路径/import/无 extras，然后各执行标准库/分析镜像 × normal/stream（共 **8 次实际 Docker SDK 示例**）。八份 report.json 全部 completed /2 次确定性模型请求；CSV/hash 与分析 PNG400×300 均正确，下载 Runtime 没有 backend。日志 `.drov/t10a-runtime-confirmed-installed-py311.log` / `t10a-runtime-confirmed-installed-py314.log`，配置源为当前非秘密环境和实际 image_id。包不附带云后端空壳或入口点，正常包检查不隐式运行 Docker。

安装示例之后按独占 name=drov-python 查询无容器残留；只查询本轮名称，不清用户其他资源。Git diff --check 通过，品牌/uv.lock/P4 fingerprint/core DDL 不变；源码/新文件/文档跟踪范围检查后提交。首个 Runtime 实施提交 [cbce832](https://github.com/drov-ai/drov/commit/cbce8327437c03c17a72c95033e2fcd2584b8f3b) 已推送，其 [CI 38570634938](https://github.com/drov-ai/drov/actions/runs/38570634938) 10 jobs 全部 success；之后核查/恢复修正 [2d768f7](https://github.com/drov-ai/drov/commit/2d768f791a4c3ee754426c8a8245d11c8778d921) 已推送，最终 [CI 38572351467](https://github.com/drov-ai/drov/actions/runs/38572351467) quality、Linux3.11–3.14、macOS/Windows3.11/3.14 与3.15 advisory 共10 jobs全部success。实际状态在 .drov/t10a-runtime-final-run.json / final-jobs.json / final-ci.json，网络 EOF 期间状态不作失败/成功判断，恢复后以实际API确认。普通核心CI未配置Docker，9个真实用例跳过；不能称为T-10B Docker专项CI。前批 078009337ffda2d4205d587e03c48e11a4438427 的 [CI 38553661046](https://github.com/drov-ai/drov/actions/runs/38553661046) 10 jobs success，仅证明前批；不得用它冒充本批 Runtime 新代码。

保留 SQLite、配对 bytes/store/stage 清单、原租约和非秘密镜像配置以恢复。发布失败先解除具体存储故障，再用原 Agent/revision、backend 与 ArtifactStore 调用 recover；只查/清/发布原事实，不删除租约或新建替代容器。未知创建且无可靠原身份时保持未清理/容量/关闭失败，不猜执行未发生。后续 T-10B 使用独占测试资源/受控故障，不停止用户共享 daemon。

未读取/输出真实 Key，未调用付费模型/E2B，未推送镜像、上传 PyPI、公开发布版本或部署官网。Git 同步已由用户授权；实际提交与远程状态在 progress 记录。下一轮无需新仓库/账号/model ID/预算，用户保持 Docker Desktop 开启即可；T-11/T-16 live 前才需模型服务/可访问 ID、安全本地 Key 引用和共同预算。

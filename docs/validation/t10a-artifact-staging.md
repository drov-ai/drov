# T-10A 首批：私有字节暂存与固定分析镜像

- 日期：2026-10-14（Asia/Shanghai）；Local `/Users/yanlong/projects/drov`，起点 main / 67ef5b2，工作区干净。先读 AGENTS/progress/导航/handoff、ADR-0016、API/运行/工程/质量/任务与 T-10/准备记录。
- 前置复跑：T-10 原全库 Python 3.11 **611 passed / 2 skipped，77.39s**；旧 T-10 已完整验收，无需跳过遗留项。首批按任务卡分批顺序交付，不关闭整卡。
- 环境：macOS 26.6.2 ARM64，uv 0.13.0；宿主 Python 3.11.9 / 3.14.3；Docker CLI/Engine 27.0.3、Desktop 4.32.0，Linux ARM64 VM。当前基础镜像/显式 seccomp 与准备快照再次复核，未更改 daemon。
- 决定与实际入口：[ADR-0017](../decisions/0017-private-artifact-staging.md)、[字节层源码](../../src/drov/artifacts/local.py)、[值类型](../../src/drov/artifacts/types.py)、[镜像 recipe/边界](../../sandbox/README.md)。

## 已验收子范围

| 对象 | 实际证据 |
| --- | --- |
| 私有存储 | 构造无 I/O；open 后 POSIX 0700 目录/0600 文件、单链接与排他所有权；SQLite artifact_store_id 与 store header 双向配对，不同数据库或不同目录拒绝；旧 records DDL/schema=1 不变 |
| 预留/写入 | 持久阶段台账先于字节；串行门/排他锁下 owner/store 预留；16KiB/批清单逻辑收费包含空批；fsync 文件、原子 rename 清单、fsync 目录，跨重开保留暂存及未完成占用 |
| 输入/字节安全 | NFC 安全名称、重名/路径逃逸校验；模型 JSON/repr 无 bytes；宿主只用 ID 路径；不跟随 symlink/hardlink，不阻塞 FIFO；类型/权限/长度/hash/读前后文件状态复核 |
| 取消/错误 | 取消等待真实 worker 完成才释放门/锁；磁盘发布失败保留 reserved 台账/部分文件，不可读、不重写；独立进程硬终止后原完整阶段/字节可重开；猜 hash/跨 tenant/user/Agent 无内部字节 |
| 安装/导入 | Artifact 模块导入受既有无文件写入/网络/子进程/线程/loop 探针检查；两解释器各两套干净核心安装从 site-packages 实际运行私有暂存/重开检查；SDK 不依赖分析库或 Docker |

新增 [test_artifact_staging.py](../../tests/test_artifact_staging.py) **38 项**。Python 3.11：**649 passed / 2 skipped，89.84s**；Python 3.14：**651 passed，94.86s**。两项 skip 仍是 Python 3.12+ 即时任务 API，不是缺 daemon 跳过沙箱验收。新增字节层测试在非 POSIX 明确不适用；这不表示 Windows Artifact 字节层已验收。

Ruff/格式（138 文件）、严格 mypy（82 文件）、生成 schema 检查、本地 wheel/sdist/从 sdist 重建、两解释器共四套干净核心安装与五个既有示例普通/stream、Twine strict 均通过。ArtifactRef/Info/Limits schema 为真实新增；ArtifactInput 是宿主 bytes 类型，不提供 JSON 上传 schema。分析资源进入 sdist，SDK wheel 只含值类型/字节层；没有新的 SDK 核心依赖。文档检查 57 个 Markdown/461 个相对链接/14 个 Python AST 片段，错误 0；AST 不等于 SDK 执行。

## 实际分析镜像与可信 CSV/PNG 探针

| 记录 | 实际值 |
| --- | --- |
| 基础平台 manifest | `docker.io/library/python@sha256:dabc8c628ee25179a9fcb8ab81e592938ee2bad3ffb15e64157f8fe428d5279f`，Linux ARM64，Python 3.11.17；原标准库镜像未改 |
| 本地分析 image config ID | `sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45`；`RepoDigests=[]`，未推送镜像，不是 registry manifest/index digest |
| 依赖 | 12 个实际 wheel；numpy 2.4.6、pandas 3.0.6、matplotlib 3.11.2；全部精确版本/PyPI URL/hash 见 [清单](../../sandbox/analysis/wheels-linux-arm64.json)，许可原文声明/文件 hash 见 [inventory](../../sandbox/analysis/license-inventory-linux-arm64.json) |
| 锁 hash | requirements-linux-arm64.lock SHA-256 `0530e6ececc90f6259f86c943f8d205c57a0448bc807dc745dde7f1e52787a25`；license inventory SHA-256 `1acf11ee8e89756d1b1be11a71c9bd9292d157303a9c0583a8c643480219e8b8` |
| 构建 | 实际本地下载/hash 核对后，Docker legacy build `--network=none`、pip `--no-index --require-hashes` 安装；保留上游 dist-info 许可。没有执行期安装、registry 推送或云资源 |
| 探针控制 | UID/GID 65534；network=none，无 binds/devices/ports；rootfs 只读，cap-drop=ALL/no-new-privileges，显式未修改 Moby v27.0.3 seccomp；有限 tmpfs/log=none |
| 实际内核值 | Seccomp=2、NoNewPrivs=1、CapEff=0；memory.max=536870912、memory.swap.max=0、cpu.max=`100000 100000`、pids.max=64 |
| CSV | `value\n12\n18\n30\n` → 实际 numpy/pandas → `count,sum,mean\n3,60,20.0\n`，25 bytes；hash `c1374df62a3a24c5102ef00107866049bf9ff7d38c39a0c510ae4fa4848a7a06` |
| PNG | matplotlib 实际生成/Pillow verify，400×300、8883 bytes；hash `8258edd76d9fca04ef58a08c3c46292906b8aaacce09a1541c7e2aebda22ec46` |
| 销毁后字节 | inspect 为 exited/0，确认删除且按原 ID 查询为空；随后输出暂存，关闭并以新 SQLite 实例重新打开 store，读回两份字节与保存 hash 相同 |

**这是执行者可信固定代码的镜像/字节层探针，不是任务卡要求的 Agent/授权闭环。** 文件传输为固定测试协议，未提供生产安全导出器。没有只读输入区、审批、Run 授予、模型工具提议、同一 Agent 循环、ready 元数据/原工具结果共同发布或动态授权下载。暂存发生在探针销毁后，不能替代最终“销毁前可靠暂存 → 销毁确认 → 事务发布”链。没有恶意代码隔离、超限、遗留子进程、应答丢失或 SDK 沙箱恢复成绩。

## 执行命令与失败修复

```sh
.drov/tooling/bin/uv run --locked pytest -q
.drov/tooling/bin/uv run --offline --locked pytest -q
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --offline --locked \
  --python /opt/homebrew/bin/python3 pytest -q
.drov/tooling/bin/uv run --offline --locked ruff check .
.drov/tooling/bin/uv run --offline --locked ruff format --check .
.drov/tooling/bin/uv run --offline --locked mypy
.drov/tooling/bin/uv run --offline --locked python scripts/generate_schemas.py --check
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --out-dir .drov/t10a-store-dist-py311
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 \
  --out-dir .drov/t10a-store-dist-py314
```

镜像准备/可信探针的实际命令见 [sandbox README](../../sandbox/README.md)，都用本地 CLI/明确 socket/独立空 auths/proxies 配置，未读取用户 Docker auths。实际准备失败与修复：

- shell 默认 PATH 没有 uv，改用项目已有 `.drov/tooling/bin/uv`，没有安装或升级工具。
- Python HTTPS/uv 在线重建曾 TLS EOF；已有锁定构建依赖在 cache 中，离线构建成功；wheel 传输换系统 curl 并有限重试，未关闭 TLS/hash 校验。未把失败请求记成成功下载。
- Wheel 许可 header 有编码的 email Header 对象，转成字符串后完整生成 inventory；不丢非 ASCII 许可声明。
- 独立 CLI 配置下无 buildx，BuildKit 首试失败；实际 Engine 27.0.3 legacy builder 以离线 RUN 成功构建。没有安装插件、弱化执行探针控制或改用户 daemon。
- Ruff 指出历史 API 草案 Python 片段排版，按现行格式修正；真实运行代码与未实现设计片段仍分别说明。

本地日志：`.drov/t10a-baseline.log`、`t10a-store-final-py311.log`/`py314.log`、`t10a-store-package-py311.log`/`py314.log`、`t10a-analysis-prepare.log`、`t10a-analysis-image/{build.log,image.json,license-inventory.json}`、`t10a-analysis-probe/probe.json`。这些是当前 checkout 的忽略文件，不自动随 Git 复制。

## 剩余缺项与恢复步骤

1. 同一 T-10A 接入 Runtime ArtifactStore 资源、事务元数据、输入字节接收/ready/Run 授予/去重、输入清单上下文、get/read_artifact 动态授权及撤权。空文件调用/旧 schema=1 兼容、跨归属与跨 Session 复用、接收提交失败需真实 SQLite 证据。
2. 实现 PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult、真实能力预检/固定配置指纹、原代码/文件审批、执行键/派发预算/持久租约；输入根及祖先保护、可信安全导出、PNG 解码/限额；销毁前暂存与销毁后 ready/原工具结果共同提交。同进程两次独立调用、审批前创建 0、原操作不重派发、非法文件无悬空引用均尚未跑。
3. 整卡完成后才进入 T-10B：真实恶意隔离/资源/取消/子进程、RPC 丢应答、暂存/销毁/发布故障与 SDK 硬终止重启、Linux Docker 专项 CI/干净安装。普通核心 CI 或当前可信探针不能替代这些门槛。T-10C 未建空模块，不需要 E2B 账号/Key。
4. 接续先核实 Git/实际源码与 `.drov/sandbox-environment.json`、analysis image.json，按记录 ID inspect；本地 image 缺失按 recipe 重建并保存新的真实 ID。字节目录与 SQLite 共同保留；缺清单/临时项 fail closed，没有自动恢复/删除接口，本批不会假装重新执行/发布 ready。遗留 stage 保留容量，后续按明确台账接入恢复，不手工改 state 或删字节。

无新外部资源阻碍：保持 Docker Desktop 开启即可。无需新仓库、账号、模型 ID、Key 或调用预算；这些资源的需要时点仍见 progress。下一轮继续本会话做输入/授权子范围。

## Git / 远程

本轮提交/同步检查点与远程结果在 progress 更新；当前记录的两解释器/镜像成绩是本地实测，不从旧 CI 推断新代码或完整沙箱通过。没有公开发布/部署或付费调用。

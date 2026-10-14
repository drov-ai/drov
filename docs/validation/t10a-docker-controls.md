# T-10A 第三批检查点：内部 Docker 控制与预检

日期：2026-10-14（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`、main / d45bd24，工作区干净。读取 AGENTS/progress/导航/handoff、任务/前置与验收、源码/最新输入验证及 ADR-0016/17/18；确认 T-10 整卡验收、T-10A 前两批验收，整卡仍进行中。复跑原全库：3.11 **683 passed / 2 skipped，84.58s**。

## 实际交付与边界

| 对象 | 已验证事实 / 限制 |
| --- | --- |
| 内部资源 | `drov.sandbox._docker` / `_profile`，构造无 I/O；open 只读 info/version/image，空私有 CLI 配置/有限环境/可信本地 socket/CLI，失败关闭与并发关闭保留进程所有权 |
| 固定配置 | 严格 DockerLimits、Linux/cgroup v2/API/资源能力与同架构固定镜像检查，绑定实际 CLI 内容/Engine/image/profile/限额的内部 hash；**尚未绑定 Run/审批/恢复** |
| 创建前/后检查 | 固定 argv 拒绝模型 flags；实际 created 资源身份/控制/展开的 seccomp profile 核对；有效 seccomp/cgroup/非 root/rootfs 由可信探针实测，**尚无 Runtime 任意代码派发** |
| 有界控制进程 | 2MiB stdout+stderr 合计、边读边限/独立期限、取消与退出后遗留管道/真实子进程组收尾，不输出原 CLI 诊断；这不是容器停止证据或沙箱 stdout 64KiB 接点 |
| 真实 Docker | 标准库和分析镜像各两次固定 sum=5050 探针，无跨调用 `/work/sentinel`；有效 memory.max=536870912、swap=0、CPU=100000/100000、PID=64、Seccomp=2、NoNewPrivs=1、CapEff=0、UID=65534、root readonly；确认本次资源销毁 |
| 包与许可 | 官方 Moby v27.0.3 profile/原 LICENSE 无修改复制入 wheel；hash/两份一致/许可检查、无 Docker 核心导入和构造检查；`.gitattributes` 保留原字节，不改 uv.lock/schema/DDL/品牌/P4 指纹 |

固定代码脚本 [check_docker_backend.py](../../scripts/check_docker_backend.py) 仅接显式本地配置，不接 code。创建前 fixture 台账可靠落盘；失败只查/清理原名称与标签，创建应答丢失且查询不存在仍记录 cleanup_unknown，不当作从未执行/已销毁。台账是测试证据，**不是生产 Runtime 的持久租约**。

## 检查与实际失败修正

新增 [test_docker_controls.py](../../tests/test_docker_controls.py) **74 项**。涵盖有限/可表示限额、mutable image/跨平台/自动 volume/环境拒绝、Engine 控制不足、创建检查时资源/安全配置改变、有效内核控制不符、真正 CLI 大输出/超时/取消/遗留子进程及关闭竞争。最终 3.11 **757 passed / 2 skipped，94.50s**；3.14 **759 passed，100.13s**；Ruff/格式、mypy 92 文件、公开 schema 检查通过。沙箱协议替身测试只证明控制路径，真实 Docker 成绩由独立探针提供。

实际失败：

1. 前两次 Docker 探针拒绝 SecurityOpt：inspect 返回展开且压缩的 JSON 而非 CLI 文件路径，修正为独立固定原字节 hash + 完整 JSON 语义比对，新增改变 defaultAction 拒绝测试；两次均未启动代码并确认销毁。
2. 第三次固定计算通过控制但环境名检查未包含 Docker 自动 HOSTNAME；只增加该非秘密内建名字，未放宽镜像/宿主凭据白名单；该容器确认销毁。
3. 首次 61 项测试与两套旧全库各有一个 fixture 失败：macOS 为测试解释器自动添加 `__CF_USER_TEXT_ENCODING`，以及 `/var` 与 `/private/var` 路径别名。仅按平台允许该测试名字、比较真实目录，仍拒绝所有 synthetic key/DOCKER_HOST 继承；最终 74 项及两套全库通过。旧全库成绩为 743+2 skip/745 passed 各 1 failed，不能记成通过。

本机 Docker CLI/Engine 27.0.3 / Desktop 4.32.0，Linux ARM64 VM；标准库 manifest `docker.io/library/python@sha256:dabc8c628ee25179a9fcb8ab81e592938ee2bad3ffb15e64157f8fe428d5279f`、Python 3.11.17；分析 image config ID `sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45`。现有镜像未重建/pull/推送；借用 daemon 与其他容器未修改。

## 命令与恢复位置

```sh
.drov/tooling/bin/uv run --offline --locked pytest -q
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --offline --locked \
  --python /opt/homebrew/bin/python3 pytest -q
.drov/tooling/bin/uv run --offline --locked ruff check .
.drov/tooling/bin/uv run --offline --locked ruff format --check .
.drov/tooling/bin/uv run --offline --locked mypy
.drov/tooling/bin/uv run --offline --locked python scripts/generate_schemas.py --check
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --out-dir .drov/t10a-control-dist-py311
UV_OFFLINE=true .drov/tooling/bin/uv run --offline --locked python scripts/check_package.py \
  --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 \
  --out-dir .drov/t10a-control-dist-py314
```

实际容器命令见 [sandbox README](../../sandbox/README.md)。忽略日志 `.drov/t10a-control-*`、`t10a-execution-baseline.log` 与 probe 的 report/lease 只在当前 checkout，不提交。中断后按原 fixture 台账 name/labels/image 核对原资源，身份不符不删除；查询不明保留未知事实，禁止重新创建原计算或按共享镜像批量删除。不删除既有 `.drov/` Artifact 台账或 SQLite 以宣称恢复。

## 未完成

PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult 公共入口、专用 Runtime 派发、代码/文件审批绑定、持久执行键/预算/资源租约与恢复、受保护输入、安全导出/PNG/可靠暂存、销毁后 ready/工具结果共同发布仍未实施。并发/执行期限只是内部配置，尚未接入 Run 预算或持续执行名额；没有完整 CSV→Python→CSV/PNG→同一 Agent→授权 hash 下载。T-10B 恶意隔离/超限/取消/硬终止与 Linux 专项 CI 未做；T-10C 未建模块。

**验收的是内部预检/控制子范围，不关闭 T-10A。** 没有模型 Key/真实数据/付费 API/包发布或部署。资源仍足够继续审批与执行模块；详细状态、下一步与 Git/CI 统一在 [progress](../planning/progress.md)。构件/安装与远程结果仅在实际完成后追加，不从历史 CI 推断本次验证。

检查点补记：两解释器 wheel/sdist/重建、共四套干净核心安装、六示例普通/stream（文件字节仅 POSIX）、Twine strict 通过；最终构件 SDK wheel hash `04ec1729f3dfdfe6b0b4c50e7e3717cffeee21ece1fcd2bdb883d3218ac25a20`。独立 3.14 core-only wheel 安装从已安装私有模块再执行两次真实分析镜像控制探针并确认销毁，日志 `t10a-control-installed-probe.log`；最新源码探针在 `t10a-control-{analysis,stdlib}-accepted/`。59 个 Markdown/483 链接/14 Python AST 错误 0，这不是 SDK 运行证明。用户同轮要求继续未完成部分，保存此检查点后继续审批/执行模块，尚无本批新远程 CI 验收。

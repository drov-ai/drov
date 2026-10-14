# T-10B：真实 Docker 隔离、资源与故障恢复

日期：2026-10-15（Asia/Shanghai），接续 2026-10-14 的工作。Local `/Users/yanlong/projects/drov`、main / dbd2a40 起点，工作区干净。已读 AGENTS、progress、README/handoff、任务前置、工程/运行/质量专项及 ADR-0016/0019/0020。复核前批远程 CI 38572351467：completed/success、2d768f7；前置本地复跑 **802 passed /2 skipped，95.08s**，`.drov/t10b-baseline.log`。T-10A 没有未验收缺项，直接实施 T-10B，未重开 P4。

## 实际交付与当前验收边界

决定见 [ADR-0021](../decisions/0021-sandbox-fault-boundaries.md)。新增真实 Docker 隔离/资源、故障和独立 SDK/stream 硬终止测试；修复监督程序启动竞态、恢复成功事件缺 result、恢复绕过原结果字节限额及文件接收 OS I/O 异常映射四个问题，并补齐固定API1.41的服务端最低版本预检。未改公共 schema、SQLite DDL/扩展版本、uv.lock、固定 runner/hash、P4 fingerprint 或品牌。

Linux AMD64 wheel 与许可清单已经实际下载/核验，新增显式双平台准备参数、Docker 专项工作流和 core-only wheel 实际安装检查。**T-10B 本地完整矩阵、最终构件/安装和最终源码9a4fba1的核心10项及原生Docker4项CI均通过，整卡验收完成。** 任务状态仍只维护在progress；以下增量记录保留失败与修复，不用旧CI/skip代替专项。

## 真实专项矩阵

| 对象 | 实际方法与断言 |
| --- | --- |
| 只读输入 | 代码 UID 65533 对可信 UID 65534 的文件写入、unlink/chmod、目录 chmod/rename、替换根、链接和 rmdir 全部失败；实际原字节/hash 不变，可导出独立副本 |
| 宿主/凭据/网络 | 合成宿主文件与 socket 路径不可见，合成环境值不传入容器；host TCP 哨兵连接计数 0，宿主阳性连接为 1；容器无 IPv4 路由，无 capabilities；不接触真实 Key |
| 输出路径/集合 | 父 symlink、输入 symlink、替换 outputs 根、socket/目录、单文件与合计超限、既有 hardlink/FIFO/畸形 PNG 全部无 ready 输出；真实 exit 保留；集合不部分发布 |
| 硬资源 | 实际 cgroup CPU 节流/使用量，64MiB 下 256MiB 内存申请不能发布部分文件；PID=12 时第128次创建前 EAGAIN；/work=1MiB、/tmp/shm=16MiB 文件风暴实际 ENOSPC；原始 stdout/stderr 有界 |
| 进程树 | 脱离进程组的延迟写者在安全导出前被停止；超时与既有 cancel 销毁整个容器；运行中 SIGKILL 前用独立 Engine inspect 确认 exec Running，再由新 SDK 查/清原资源 |
| 客户端故障 | 实际创建/代码/导出/销毁后丢应答，或销毁前故障；原 create/start 至多一次，销毁不明保留 active/名额，第二 Run 容量拒绝；verify 只查，recover 复用原阶段 |
| 持久故障 | 真 SQLite trigger 拒绝 ready 发布使结果/引用全回滚；解除后发布原暂存，无第二次代码；原字节 fsync/最终阶段清单 ENOSPC 不发布文件，预留/未完成清单保留计量 |
| 恢复限额 | 512-byte 原结果上限，销毁中断后 recover 仍拒绝 2KiB stdout 结果；failed_known/output_unavailable，无 ready 输出，不重算 |
| 不可确认创建 | 独立 SDK/stream 进程中不可靠创建应答且实际查询 absent；unknown/active=1，关闭 ShutdownIncomplete，SQLite 锁仍持有；父进程结束后台账仍 unknown，不宣称未执行或释放持久容量 |

SIGKILL 屏障包含输入 staged/接收提交、container create 应答/身份提交、输入安装、exec create intent/应答/身份/start intent、运行中/结果应答、观察提交、输出预留/计划/字节 fsync/staged、销毁应答/事实提交、共同发布。9 个事务边界分别在 COMMIT 前/后停止，其余按实际外部/字节边界停止，SDK/stream 对照。SQLite integrity、state/checkpoint/event 版本/序列与会话所有权逐例核对；硬终止不等于物理断电。

持久控制日志活在 SDK 进程外的文件系统，记录已返回 create/exec 与 start 尝试的上界及代码完成应答。恢复日志不能新增 create/exec/start；代码运行中另由父进程 Engine 查询确认。它不是独立业务计数服务，也不把未知 start_attempt 当成已完成效果。

## 实际环境、检查与日志

本机 Docker Desktop Linux ARM64 VM/Engine 27.0.3/cgroup v2、固定标准库与分析镜像沿用 T-10A。分析镜像 config ID/RepoDigests 区分、固定 Moby v27.0.3 seccomp/许可保留。实际运行 Python 3.11.9 与独立 `.drov/check-py314` 的3.14.3，避免 uv sync 并发替换 `.venv`。

- 初批真实隔离16项通过；丢应答/SQLite/磁盘8项通过，补恢复限额后9项通过（`.drov/t10b-faults-final.log`：9 passed /7.90s）。
- 第一批36个硬终止通过，运行中补验后38个通过；进一步补齐执行意图/身份、字节 fsync 后，共56个硬终止；未确认创建关闭保留另2个。
- 增量阶段未含最后2个保留用例的专项：**90 passed /795 deselected，107.89s**，`.drov/t10b-docker-final311.log`。最终全库/静态/包/安装与远程结果见最终验收节。
- Ruff/格式172 files、mypy110 files、schema check 通过；不会把静态通过当作实际代码/模型质量。

```sh
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" \
  .venv/bin/python -m pytest -m docker
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" \
  .venv/bin/python -m pytest
```

专项 CI 设 DROV_REQUIRE_DOCKER=1，缺配置直接失败。核心 CI 不提供 Docker 配置时明确跳过，不能以绿色核心矩阵关闭本卡。真实模型、MCP/HTTP/UI/E2B 尚未覆盖；T-15 备份/维护也未完成。

## 失败、修复与收尾

1. 首个只读输入试验漏选工具 inputs；ScriptedStep 的 ModelComplete 会重新验证/复制对象，改为选择实际事件中的调用。记录首次失败，不改生产授权规则。
2. 容器 Linux errno 与 macOS 宿主不同，rename 跨 tmpfs 为 EXDEV，使用实际 Linux错误值；未把正确拒绝误判成隔离失败。
3. CPU=0.1 暴露监督文件尚未创建，按 ADR-0021 修复；无效控制信息仍拒绝。实际CPU/PID/盘/内存矩阵随后通过。
4. 故障测试初稿误用了不存在的 SQL operation_id 列、RunOutcome.operations 和嵌套读取事务，修正夹具为实际 payload/RunRecord/同事务读取。一次测试断言失败留下测试监督容器；按完整 ID/name、合成 OWNER hash、实际 backend hash/image/固定 SUPERVISOR 逐项核验后只销毁该资源，独立记录 `.drov/t10b-fixture-cleanup.json`。未用它伪造原输出/ready 或修改生产租约。
5. 恢复成功事件遗漏原 result，由真实故障测试复现并修复。512-byte 恢复限额回归先真实失败（错误 succeeded），修复后9项故障测试通过。
6. stream 夹具初稿未进入 subscription/未迭代，修正后所有对应屏障通过。父进程有45s到屏障/独立结束期限；只操作测试归属资源，不停止共享 daemon。
7. PyPI TLS曾中断，保留失败，按固定 URL/hash 重试成功；没有关闭 TLS、变更依赖版本或读取凭据。本机默认 uv 为0.11.30，包检查先明确拒绝；找到并使用前批 `.drov/tooling/bin/uv` 0.13.0，未升级用户全局工具。

8. 未确认创建补验首次在3.14退出时发现原生 SQLite 查询夹具未关闭（2项断言通过但进程失败），改为显式 closing 后2 passed；不是 SDK存储失败，也未忽略警告。
9. 两套最新全库并行时，3.11 的旧控制夹具0.5s把非法JSON分类提前变成deadline（1 failed/885 passed/2 skip）；3.14完整888通过。该用例检验错误分类而非解释器启动速度，改为即时错误5s、故意hang仍0.5s，保留有限控制/真实超时断言；重跑对应组和3.11全库。
10. 补强 cancel：先由真实 Engine inspect确认原exec Running，并用可信只读探针确认wrapper/原child/脱离组child至少3个，再cancel；两套解释器均实际通过，无后续模型调用。

## AMD64 来源与原生验收

实际从 PyPI 每个固定版本 JSON 选择 Linux AMD64/CPython3.11 wheel，下载核验全部12项；版本与 ARM64 相同，平台 wheel/hash/许可文件单独保存。来源是各包官方 PyPI 发布对象，记录在 [AMD64 清单](../../sandbox/analysis/wheels-linux-amd64.json)、[锁](../../sandbox/analysis/requirements-linux-amd64.lock)、[许可](../../sandbox/analysis/license-inventory-linux-amd64.json)。同一固定官方 Python index 0a310eee…实际解析的 AMD64 manifest 为 `sha256:8a00ecb83ed9d0864dfd102a1bdb2067fda907ceb30bb86d09da9ee558b19590`；ARM64 dabc8c…不冒充 AMD64。下载/许可不等于镜像或原生执行已经通过。

工作流在独立 Linux runner 上准备固定 base、预下载/hash wheel、network=none 构建，实际专项和干净 core-only wheel 标准库/分析×SDK/stream。配置记录和测试XML仅非秘密合成数据；不发布镜像/包/网站。Docker API兼容事实核对来源为 [官方API版本矩阵](https://docs.docker.com/reference/api/engine/)（查阅2026-10-15），不能用版本表代替本工作流实测。

最终全库/构件/安装/远程实际结果见下节；整卡验收完成，仍排除本卡之外的live/服务/维护。用户无需新仓库/账号/Key/model ID/预算，保持 Docker Desktop 开启即可。若换机器，保留原 SQLite+bytes+租约/config，按 recipe 准备对应架构固定镜像；未完成阶段不能删后重跑。下一任务与会话建议仅在 progress 维护。

11. 最后3.11全库的低 CPU 试验因10s包含容器/监督/wrapper启动而得到真实timeout（1 failed/885 passed/2 skip），不是CPU配额失效；节流验证保留0.1CPU，执行期限明确25s，独立2s超时/进程树用例仍保留。
12. 追加丢文件/hash损坏与输入清单ENOSPC/接收SQL拒绝：首次发现公开文件接收传出了原始OSError，按ADR-0021在接收边界映射PersistenceUnavailable；私有阶段异常契约保持。修正后真实文件/资源/故障与接收相关 **64 passed /37.66s**（`.drov/t10b-file-resource-final.log`）。输入失败Run/ready/容器/模型0，损坏暂存恢复failed_known且原代码不重做。

13. 官方API版本矩阵显示部分Engine最高API虽≥1.41却不接受1.41；执行器仍固定该子集。预检补查MinAPIVersion，缺失/非法/高于1.41启动前拒绝，新增5项无daemon契约测试；本机实际Min=1.24，已有支持配置/hash不变。不宣称支持所有更高Engine版本。

## 最终验收、构件与远程

最终源码 **9a4fba12d09ffd02f194936fa2caf635b7cf6b03**，新增 **93**（88个真实Docker、5个固定API范围契约），总 **897**。真实Docker组97个：原9基础、17隔离/资源、13实际故障、56硬终止+2不可确认创建关闭；集成中其他授权/字节/Schema契约由对应真实SQLite/文件组补充，BackendFixture不冒充Docker执行。

| 最终检查 | 实际结果 / 日志 |
| --- | --- |
| 完整3.11含真实Docker | **895 passed /2 skipped，256.81s**；仅两个3.12+ API不适用；.drov/t10b-final311.log |
| 完整3.14含真实Docker | **897 passed，269.15s**；.drov/t10b-final314.log |
| 静态/schema | Ruff174 files、mypy110 files、schema check通过；无schema/DDL/uv.lock/runner或历史指纹变更 |
| 两解释器构件/安装 | wheel/sdist及sdist重建逐文件比对；四套全新core-only安装、六示例各SDK/stream与Twine strict通过；.drov/t10b-final-package311.log /314.log |
| 安装后真实执行 | 两个全新非editable/core-only wheel，在仓库外 -I/-B 确认实际site-packages/no-extras；标准库/分析×SDK/stream共8次真实CSV/PNG/hash/销毁后下载；.drov/t10b-final-installed311.log /314.log及8份report.json |
| 最终核心CI | [38600296733](https://github.com/drov-ai/drov/actions/runs/38600296733) quality、Linux3.11–3.14、macOS/Windows3.11/3.14与3.15 advisory共10 jobs全success；3.15不扩成支持承诺 |
| 最终Docker CI | [38600296414](https://github.com/drov-ai/drov/actions/runs/38600296414) Ubuntu24.04原生AMD64/3.11–3.14四jobs全部success，每套 **97 passed /800 deselected，无skip**；固定分析构建/两套构件安装/安装后4次真实样例均通过 |

两解释器最终构件一致：wheel `96000425939d27e419bd37a42d4ad0b7d71ddd6daf44f9519bcefb2b296bb4e7`，sdist `97a8ed7e577d82c2a6d724f4635abd95b2b5be12d2e232078951e2226d926900`。全部drov/ wheel内容逐字节等于最终src/drov。构件在本验收补记前生成；补记只改文档，不把随后sdist称为同一hash。之前16c62e…/31bff3…为中间构件，不替代最终版本。

Docker四套真实专项耗时：3.11 133.12s，3.12 185.78s，3.13 180.12s，3.14 184.50s。其本地分析image config ID各自为3a8c6d…、51f535…、133aab…、a6eef1…；构建时间导致ID不同，不声称bit-for-bit相同或registry发布。完整非秘密日志 .drov/t10b-final-docker-ci.log，最终API状态与各job清单 .drov/t10b-final-{docker,core}*.json。

普通核心CI没有Docker配置：Linux/macOS3.11 **798+99 skip**，其余已支持POSIX **800+97 skip**；Windows3.11 **693+204 skip**，3.14 **695+202 skip**，多出的105是POSIX字节/控制/Runtime适用跳过，另2个为3.11版本限制。不将Windows核心安装成绩当作Windows Docker或Artifact支持。native Linux的97项由专项独立实际执行；日志 .drov/t10b-final-core-ci.log。

fbc45b8、9ba9ce3、62a5072、9a4fba1已按用户授权提交/推送main；中间workflow因新提交取消是取代检查点，不冒充最终失败或成功；9ba9ce3和62a5072的Docker四jobs也成功，最后采用9a4fba1。检查敏感模式与跟踪范围，未提交.drov/真实凭据或品牌；无PyPI/镜像/网站发布。最终git diff --check/文档链接与14片段AST通过，源码之后无变化。

最后按测试名称查询无drov-python容器残留；保留安装样例原SQLite/bytes/ready引用和各阶段台账。故意不可确认创建的测试无实际新容器，持久lease仍unknown/active；这体现契约而非需要自动清空的生产成功记录。未ready、损坏或失败预留继续计量，不能凭测试收尾手删后称为公共维护；T-15才提供回收/备份。新Runtime恢复需原Agent/revision、backend配置和配对字节/库，先解除具体IO故障，只查/清/发布原证据，不重建原执行。

本轮没有模型Key/私有数据、付费模型/E2B调用或公开发布/部署。仅证明受测本地ARM64 VM与原生AMD64 Linux配置的框架/隔离/恢复，不证明真实模型质量、性能领先或敌对公网强隔离；HTTP/UI/MCP、云profile和完整维护/备份仍未验收。下一项/资源/会话建议统一见progress。

最终补记文档检查：63 Markdown、524相对链接、14 Python AST片段，错误0；git diff --check通过。只补记Markdown，与9a4fba1之间SDK/测试/工作流/锁/DDL完全不变；原验收构件与CI仍对应9a4fba1，README/验收补记不混称为同一sdist。

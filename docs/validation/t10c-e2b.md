# T-10C：可选 E2B 单次执行验证

日期：2026-10-15（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`、main / e5f0a16，干净且未更换 checkout。T-10B/T-11 已完整验收，用户明确选择先补 T-10C，并授权已有 E2B 项目/凭据、合成测试和赠金额度；没有模型、公开发布或部署授权。决定见 [ADR-0023](../decisions/0023-e2b-single-execution.md)，任务状态见 [progress](../planning/progress.md)。本记录在最终回归/安装/CI完成后补记，不能从早期检查点推断整卡关闭。

## 环境、资源与费用

- macOS ARM64；本地 Python3.11.9 与独立3.14.3，原 uv0.13.0；保留原 Docker/SQLite/bytes/品牌/P4指纹。前置含真实Docker全库 **957 passed /2 skipped，219.14s**，`.drov/t10c-baseline.log`。
- 凭据实际文件是 `.env.local`，不是用户文字中的 `.local.env`；确认已忽略并从644收紧到600。仅授权宿主解析 E2B_API_KEY，不向模型显示，不 source 文件，不复制进模板/沙箱；两种文件名均已忽略。项目区域截图声明us-west-1，SDK不提供区域证明，不据此声称驻留验证。
- 实际 SDK **e2b2.56.0/MIT**；其HTTP/RPC/原生传递依赖和构件hash在 uv.lock。模板为 `0k17pnwmk83ny9je1633:347a1104-f90d-4644-8024-12fc30619da0`；仅本账号私有测试资源，没有公开模板或镜像发布。固定Python AMD64基础manifest与分析包hash复用 sandbox/analysis；实测numpy2.4.6/pandas3.0.6/matplotlib3.11.2、2vCPU/2048MiB、envd0.10.0、工作盘挂载均通过。
- 首轮build `c1f1f843-133a-43a4-85ea-9a3bef146c76` 因默认user无法创建/opt/drov而失败；recipe改成先显式root后第二轮ready，旧失败ID保留，未覆盖旧证据。工作负载实际仍UID65533。模板只接收固定公开recipe/哈希锁，没有目录复制或真实密钥。
- 首批自主设 **5美元保守总上限**，单次provider寿命180s/单并发；执行profile采用20s/control40s。按100 micro-USD/s留高于查阅报价37的余量，每次18100 micro-USD，构建每次另预留1美元；失败/清理包含在预留内。`.drov/t10c-cloud-cost.json` 为跨测试数据库/子进程宿主总账；SQLite内另对budget_id同事务累计整寿命预留、不退款。**这是上界估算，未查询账号实际余额/赠金资格或供应商扣费账单。** 实付可在E2B控制台按资源/时段核对。

## 已跑检查点

| 检查 | 实际证据 |
| --- | --- |
| 首个资源探针 | `.drov/t10c-capability-probe.json`：资源ie59jvuox182v6u2i7y9r，显式网络/kill生命周期、实际规格/库；kill=true后SandboxNotFoundException |
| SDK / stream CSV→CSV/PNG | `.drov/t10c-live-sdk2/report.json`、`.drov/t10c-live-stream/report.json`：默认审批后实际sum60、summary.csv、400×300 PNG；同一Agent最终输出，销毁/关闭后新Runtime授权下载/hash；确定性fixture，不是模型质量 |
| 离线协议/拒绝 | 首批40通过；新增预算跨Runtime不退款、自有/借用排他与扩展兼容；最终数量见后续汇总。替身只证明协议/事务，不冒充E2B隔离 |
| 真实权限/网络/资源/文件 | 15通过/90.35s，`.drov/t10c-live-tests1.log`；只读输入/祖先、UID/NNP/cap/environment、envd本地未认证拒绝、外部应用层无响应、内存/CPU时限/PID/单文件/文本风暴、symlink/hardlink/FIFO/缺文件/超限拒绝、取消/丢create回复按原键清理 |
| 公共URL/到期/工作盘 | 3通过/20.39s，`.drov/t10c-live-extra1.log`；同一真实服务匿名403/宿主token200；实际缩短测试资源期限后404；/work与三个临时盘实际ENOSPC |
| 真实RPC/文件故障 | 9通过/40.88s，`.drov/t10c-live-faults1.log`；create/上传/运行/导出/查询/kill请求或回复故障，真实资源存在；原有可靠暂存可恢复，丢输出不重算；创建/代码请求计数不增 |
| SDK / stream SIGKILL | 30通过/181.84s，`.drov/t10c-live-crashes1.log`；remote_ack/start_intent/observation/destroyed/publish提交前后、create/code/kill回复、真实运行中与输出暂存；新进程重开SQLite和bytes，只查清原资源；完整原暂存才成功，无原输出failed_known，创建/启动计数不增 |

真实个案的非秘密资源ID/原lease/请求计数、SQLite与bytes保留在 `.drov/t10c-live-tests/`；不能以SDK进程退出作为VM销毁依据。普通CI不读取Key；live组只有显式DROV_E2B_TEST_CONFIG才运行。

## 实施中发现与修正

- SDK BuildInfo还要求name/alias，首个open以TypeError失败（无create）；补齐官方实际签名后通过。credential resolver异常最初可透出原异常，已映射固定ConfigurationError并加脱敏测试。
- 检查到将远端字段直接加进v1 Docker JSON会破坏旧读者；改为Docker序列化保留旧字段，显式E2B将sandbox_schema升到2，DDL相同，旧SDK前置拒绝。对应兼容测试/源契约同轮更新；历史P4指纹不改。
- root monitor补齐父进程退出后的子进程清除，运行前再次扣除准备/上传/许可检查耗时；最终云矩阵使用新runner/配置重新核对，旧检查点不代替最终证据。
- 两个早期测试夹具修正：关闭Runtime后不能再调用recover，改为直接检查controller关闭拒绝后恢复；借用排他测试补建数据库父目录。不是绕过Runtime关闭语义。静态检查的长行/类型修正按最终检查记录。

## 范围与恢复

交付同一PythonSandbox、权限/审批、Artifact、原执行恢复，不新增模型调用、持久Notebook/共享变量、公开Shell、网络安装、HTTP/UI或多Agent/Skill。区域/账单未由SDK证明；provider VM不等于Docker配置。原创建应答丢失且查询仍空会继续保留unknown/名额/关闭锁，不能凭TTL或手工响应伪造清理。

若中断：保持同checkout、固定template2记录、.env.local私有配置、费用总账及原SQLite/bytes；先按台账查询本次完整metadata/原resource ID，确认并销毁，恢复原Run，不重新运行原代码。模板仍可保留用于后续显式测试，实例必须收尾；最终远端残留查询和构件/CI另行追加。下一项T-12仅在本卡实际完整验收后恢复；其具体要求见implementation-plan。

## 最终本地汇总（远程CI仍待本轮核对）

- 最终公共SDK源码下远端矩阵 **57 passed，300.30s**，`.drov/t10c-live-final.log`；追加宿主真实合成哨兵/错误归属拒绝/销毁后两次独立环境与显式文件复用 **1 passed，8.15s**，`.drov/t10c-live-fresh-final.log`。共58项真实远端，不将默认跳过算通过。
- 含真实Docker全库3.11检查点 **1000 passed /59 skipped，213.27s**，`.drov/t10c-full311.log`；跳过为57项未显式启用的E2B及2项Python3.12+ API。追加新云测试在独立实跑后纳入最终CI，最终收集数以CI为准。3.14与安装结果后续补记。
- Ruff199文件、严格mypy130文件、schema、文档67份/544相对链接/14 Python AST均通过；`.drov/t10c-doc-check.json`。SQL表DDL/core record/user_version与历史P4指纹未改，生成schema仅加E2BLimits，sandbox参考DDL注明v2 metadata。
- 两解释器wheel/sdist/重建逐文件比对、四套core-only安装及六示例SDK/stream通过；`.drov/t10c-package311.log`、314.log。两次构建期间验收文档继续补记，sdist分别记录当时文档，不能宣称跨时点相同；SDK字节一致。两套独立extra安装/仓库外只读preflight通过，不调用云，`.drov/t10c-installed-offline311.log`、314.log。
- 真实旧T-11 wheel安装到独立环境：读取本轮新Docker v1 Run/lease成功，打开E2B扩展2以IncompatibleStorage拒绝且数据库hash未变，`.drov/t10c-legacy.log`。不是只用新版模拟旧解析器。
- 锁内protobuf-py-ext0.1.1的wheel/sdist遗漏许可元数据；已下载并校验sdist hash，14份代码/stub/build文件及Cargo.lock/README/pyproject逐Git blob hash匹配上游固定snapshot9f963031，根LICENSE确认为Apache-2.0。Cargo.toml为打包规范化、PKG-INFO生成，未宣称原生二进制可重复构建。完整事实、SHA与许可边界已记THIRD_PARTY，原始证据`.drov/t10c-protobuf-source-license.json`；未编造该wheel的License-Expression。
- 本轮最终构件当前wheel `2e3f030cecd0680ff544cd8e225c42cf3434b2d453126bb31876fa259ed9b5b7`，sdist `1ed6accc130ed27153641a2a4d815e82742470f75d33ed48fd684d4fa3ee43bc`，Twine strict通过；wheel67个Drov文件与当前src逐字节一致。初次手工比对误纳入被忽略的.DS_Store，修正检查范围后通过，没有将该文件打包或删除用户资料。构件之后的验收补记仅Markdown，不冒充旧sdist包含后续文字。

实际主要入口（Key仅通过私有文件宿主解析，不把值写入命令）：

```sh
uv sync --locked --extra openai --extra e2b
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" .venv/bin/python -m pytest -q
DROV_E2B_TEST_CONFIG="$PWD/.drov/t10c-live-config.json" .venv/bin/python -m pytest tests/test_e2b_live.py tests/test_e2b_live_faults.py tests/test_e2b_live_crashes.py -q
.venv/bin/python scripts/check_installed_e2b.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t10c-final-dist/drov-0.1.0a0-py3-none-any.whl --live-config .drov/t10c-live-config.json --directory .drov/t10c-final-installed311
```

云入口必须继续使用原独立总账，不能删台账或复制SQLite重置费用/执行事实；普通CI不设置上述E2B变量。

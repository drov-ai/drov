# T-13 HTTP 与身份边界验证

日期：2026-10-15（Asia/Shanghai）。起点 `/Users/yanlong/projects/drov`，main / 119de61，干净且与 origin/main 同步。已读 AGENTS/progress/导航/handoff，按 implementation-plan 核对 T-10B/T-12 完整验收；实查 3a013be 的核心 38718551848、Docker 38718551936 均 completed/success，无上项缺项。未开启新一般规划。

## 实际交付与环境

按 [ADR-0025](../decisions/0025-http-identity-and-uploads.md) 接通 DrovApp/lifespan、显式本地/应用身份、同一 Runtime 的运行/交互/检查/上下文/会话和 SSE、私有上传台账/同事务消费/附件下载。核心导入不加载可选栈。源码/源契约、schema/OpenAPI、案例、工程、兼容与许可同轮同步；原 records/Artifact/Sandbox DDL、历史 P4 指纹和品牌不改。独立上传扩展会使旧 SDK 拒绝带该扩展的库，后续维护回收仍属 T-15。

本机 macOS ARM64，Python 3.11.9 / 3.14.3，uv 0.13.0。已锁安装 FastAPI 0.143.0、Starlette 1.7.0、Uvicorn 0.54.0。开发组增加既有锁中 httpx 0.28.1，用于独立 server 测试；uv.lock 仅根 dev 声明变化，无解析包版本/hash变化。所有身份、文件、工具和模型输出均合成；没有读取真实凭据、付费模型/云调用、私有上传、公开发布或部署。

## 检查点与失败修正

- 前置含真实 Docker 全库：**1067 passed /61 skipped，270.71s**，`.drov/t13-prerequisite.log`。59 项真实 E2B 缺本轮授权配置，3.11 另两项 Python API 适用跳过；不读取 `.env.local` 或动用旧云预算。
- 首轮 30 项 HTTP 为 28 通过/2 失败，夹具漏 @tool 必填 name，且对终态 recover 误期望成功。按实际既有契约修正夹具，终态 recover 仍冲突，未改内核复活终态。修正后 30 项通过。
- 首轮真实传输 2 通过/5 失败：测试客户端继承了macOS系统HTTP/HTTPS代理而未抵达loopback（环境代理变量均未配置，urllib系统代理scheme实际为http/https）；改用 trust_env=False，额外对响应状态作断言。另修正 ScriptedStep 实际事件参数，并接住 Starlette ASGI 2.4 将发送 OSError 包装后的 ClientDisconnect；没有把 Task 结束冒充内核执行停止。随后 7 项真实传输/阻塞发送通过（1.64s）。原失败日志 `.drov/t13-transport-first.log`，修正 `.drov/t13-transport-second.log`。
- 三参考场景 SDK/stream/HTTP 的 9 项对照、10 项上传错误/原子性、30 项 HTTP 和7项传输通过。补 verify/原保存 final 重开 recover/失败快照与安全500 后，离线 HTTP 合计 **59 passed，3.61s**，`.drov/t13-all-http.log`。代码模型均 ScriptedModel，不把对照当模型质量。
- 真实 Docker 三入口 CSV→统计→summary.csv/实际标准库绘图 PNG→批准/销毁→HTTP hash 下载、无 backend/Agent 的重开下载均通过：**3 passed，3.57s**，`.drov/t13-docker-second.log`。初轮误用不存在的 LeaseTransaction.active 造成3项测试失败，改为现有 active_count 断言后复测；没有跳过真实执行。
- 真实 loopback 普通 HTTP / SSE 示例均通过，三场景输出与单次写入7一致：`.drov/t13-example-http.log`、`t13-example-sse.log`。示例本地 token 只在进程中传递，不输出/入 URL/保存。

## 验收过程检查点（历史）

以下按实施顺序保留失败、修正和中间构件。各检查点的“最终”只指当时目标；本轮最终源码、构件与 CI 统一见文末，不以中间成功替代后续修正的验收。

主要实际入口：

```sh
uv sync --locked --all-extras
python -m pytest tests/test_http.py tests/test_http_transport.py tests/test_http_artifacts.py tests/test_http_scenarios.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest tests/test_http_docker.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/generate_schemas.py --check
python scripts/generate_openapi.py --check
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t13-dist311
python scripts/check_installed_server.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t13-dist311/drov-0.1.0a0-py3-none-any.whl
```

边界：HTTP单worker、精确同源Bearer认证，不自带账号/TLS/IAM。文件层沿既有LocalArtifacts的POSIX限制，Windows仍可用无文件HTTP/SDK，文件项适用跳过不代表已经支持Windows私有字节层。上传是已知 Content-Length 的原始单文件传输，不支持 multipart/未知大小；未消费/失败/过期暂存仍占容量，不自动回收；清理/备份在 T-15。下载按内核 hash/权限检查，有界块发送无法撤回已发送字节。SSE 控制错误无 durable cursor；token 不保证重放，持久事件可按原游标补读。工作台/真实模型/公开发布未做；本轮未调用 E2B，不将历史 E2B预算/质量写成本轮结果。


收尾前修正：core-only严格mypy发现可选依赖缺失时继承StreamingResponse成为Any基类。订阅所有权改由外层DrovApp ASGI请求的finally持有/关闭，返回普通StreamingResponse，避免核心类型检查依赖Web安装；两套严格类型（150文件）及59项HTTP回归均通过（4.49s），接口/行为未改。初批完整回归3.11 **1129+61skip，290.58s**；3.14 **1130+59skip/1失败，317.06s**。失败是既有Responses Retry-After日期在收集时生成，长回归运行到该用例时已老化超过原100s窗口；改为该用例开始时生成，保留真实官方客户端/HTTP日期解析与数值区间，生产适配不改。原失败日志保留，修正后两套HTTP+Responses专项均 **120 passed（5.15s/4.67s）**。追加文件发送中途撤权及未知/不符长度测试后，当前完整收集 **1192项**，新增HTTP **64项（61离线/传输+3真实Docker）**；最终全库另记，不把旧59项/初批构件冒充最终。

初批两解释器wheel/sdist重建、四套core-only新环境安装/示例及两套独立server安装后的真实HTTP/SSE三场景均通过，`.drov/t13-{package,installed}{311,314}.log`。该wheel在订阅所有权修正之前，只作历史检查点；最终构件/安装随后补记。core-only严格mypy150文件通过，`.drov/t13-core-types-fixed.log`，含extras版本同样通过。原T-12旧wheel实际仓库外安装后拒绝新upload_schema=1样本库（IncompatibleStorage），主DB前后SHA256均 **8ed238a0736bcaa3415112528a136b382f761b39ef04fae6d2f89333135924e8**，未修改原库；原本无上传扩展的旧库回归由现有存储测试覆盖。


首个推送20eb133的CI实际HTTP测试通过，但Python3.13/3.14在OpenAPI快照检查失败：FastAPI默认取标准库HTTPStatus描述，3.11的413/422短语与新Python不同。对比两份完整JSON，仅错误description不同。已将错误描述在真实路由中显式固定（同时补411/500文档），两解释器生成逐字一致；没有忽略快照差异或跳过平台。原CI为核心38726688899/Docker38726689008，作为历史检查点，不用于关闭最终卡。运行代码的安全错误形状/数值状态未变。另加强真实SSE测试：实际先订阅再释放模型，必须收到三个token.delta并逐帧检查无id，避免模型过快完成造成空断言；测试数量不变。


第二个源码检查点为已推送 **478eb1ecb5f56f561b13cbe0a875a8da93706b44**，[核心38727059007](https://github.com/drov-ai/drov/actions/runs/38727059007) / [Docker38727058909](https://github.com/drov-ai/drov/actions/runs/38727058909)。八套支持平台的HTTP独立步骤与OpenAPI生成检查实际通过，随后其余步骤也完成（核心10/Docker4均success）；之后仍发现并修正下述SSE授权窗口，因此该检查点不能关闭新修正。

第二批本地运行代码完整回归为 **1192项**：3.11 **1131 passed /61 skipped，293.85s**；3.14 **1133 passed /59 skipped，315.08s**，`.drov/t13-final-full311.log`、314.log，均含102项真实Docker（原99+本轮3）。59项skip为本轮未授权/未配置的真实E2B，3.11另两项版本适用项。之后固定路由OpenAPI错误描述并强化同数量的token测试；61项HTTP再次通过（4.51s），强化后的7项传输两套通过（1.68s/1.62s），OpenAPI两套逐字相等。该检查点全套静态/格式（223文件）/严格类型（150文件、含extras和core-only）/schema/OpenAPI/差异空白检查通过。

订阅所有权修正后的两解释器构建/sdist重建、四套全新core安装及六例普通/stream、两套独立server安装与三场景真实HTTP/SSE全部通过，`.drov/t13-final-{package,installed}{311,314}.log`。OpenAPI描述修正后的历史wheel/sdist位于`.drov/t13-head-dist/`；Twine strict与两解释器独立server安装后的真实HTTP/SSE均通过，`.drov/t13-head-{build,twine,installed311,installed314}.log`。当时wheel全部Drov文件与该检查点src逐字节相等，数值如下；最终SSE修正后的构件另记。


源码检查点478eb1e的Docker CI **38727058909已completed/success**，四个job全部success，元数据/逐步日志为`.drov/t13-final-docker-{ci.json,jobs.json,ci.log}`。Linux3.11–3.14每套 **102 passed /3 skipped /996 deselected**，耗时198.97s/174.59s/139.42s/187.60s；3skip为未安装其它可选模块的收集跳过，不是跳过102项Docker。每套随后完成核心wheel/sdist和core-only安装后标准库/分析镜像的SDK/stream实际执行与字节读取。

该历史wheel SHA256 **ce8085c73873211174010e1fd8c21c8c0ddec280694940be6d8d4aa0c1acc758**，sdist **89c5322c0c4ae445e9b278861769ec55551cb1e9df63364155e73eff4db5b274**；**75个Drov wheel文件逐字节等于当时src**。对应独立server安装后两解释器真实HTTP/SSE与Twine均通过；后续SSE源码修正已重建，不将该历史构件作为最终交付。


收尾SSE审查补充：478eb1e的核心10/Docker4 job均已success，但发现认证适配器本身可能等待，原先“RunEvents先投影→重新认证→发送”的首帧存在授权变化窗口。新增真实回归在逐帧authenticate中撤销view_content，修正前确实发送了带id的run.accepted首帧（`.drov/t13-prefetch-revocation-before.log`），未忽略该失败。现在认证固定actor后再次调用同一Runtime.visible复核读/正文权限，再交给传输；测试必须只收到无id的stream.error，不能发送已预取事件。两解释器HTTP+Responses专项 **121 passed（5.27s/5.02s）**，`.drov/t13-prefetch-fixed311.log`、314.log。新增测试使完整收集为1193、本轮HTTP65（62传输/离线+3真实Docker）；最终两解释器全库/构件/安装与远程目标将重核，不以478之前的成功关闭新修正。


## 最终源码验收（完整通过）

最新SSE修正已推送 **26b97ee34f4078e92cce8e25fba9d86c76bde20c**。本节所有最终结果均对应该源码；收尾只修改Markdown，不伪称既有sdist含最终补记。完整收集 **1193项**，本轮新增 **65项HTTP测试（62离线/真实传输+3真实Docker）**。

| 最终检查 | 实际结果 / 日志 |
| --- | --- |
| Python 3.11.9 全库、含真实Docker | **1132 passed /61 skipped，301.31s**；`.drov/t13-authorized-full311.log` |
| Python 3.14.3 全库、含真实Docker | **1134 passed /59 skipped，324.35s**；`.drov/t13-authorized-full314.log` |
| 两解释器构建与全新core安装 | wheel/sdist、从sdist重建/载荷比对、四套独立core-only安装、六例普通/stream通过；`.drov/t13-authorized-package{311,314}.log` |
| 独立server安装与真实传输 | 两解释器、三场景实际HTTP/SSE通过；`.drov/t13-authorized-installed{311,314}.log` |
| 构件检查 | Twine strict通过；`.drov/t13-authorized-twine.log`；75个wheel内Drov文件与最终src逐字节相等 |
| 代码与生成检查 | Ruff通过、223文件格式一致；含extras与core-only两套严格mypy各150文件通过；schema/OpenAPI检查与git diff空白检查通过，实际输出已核对 |
| 远程Docker专项 | [38746896088](https://github.com/drov-ai/drov/actions/runs/38746896088) completed/success，head_sha逐字核对26b97ee；Linux3.11–3.14四个job全部success，每套102项真实Docker通过，并完成core-only安装后实际执行/字节读取 |
| 远程核心矩阵 | [38746896113](https://github.com/drov-ai/drov/actions/runs/38746896113) completed/success，head_sha逐字核对26b97ee；quality、八套支持平台package和Python3.15 advisory共10个job全部success，HTTP/OpenAPI/MCP/核心回归/构建/各extra独立安装均实际通过；3.15不扩充支持矩阵 |

上述全库的59项skip是本轮未授权/未配置的真实E2B；3.11另外两项是Python版本适用跳过。两套全库均实际包含102项Docker，不把E2B skip当远端成功。当前Docker专项每套为 **102 passed /3 skipped /997 deselected**；3skip是未安装其它extra的收集跳过。逐步日志和head/job元数据保存在`.drov/t13-authorized-docker-{ci.json,jobs.json,ci.log}`。

核心最终元数据和完整逐步日志在`.drov/t13-authorized-core-{ci.json,jobs.json,ci.log}`。Linux3.11–3.14及macOS3.11/3.14每套HTTP **62 passed**；Windows3.11/3.14每套 **49 passed /13 skipped**，13项是现有POSIX字节层适用限制，不表示Windows已支持本地文件配置。八套均实际完成OpenAPI快照和独立server安装后的真实HTTP/SSE三场景；不能把核心回归卸载extra后的skip冒充已跑Web测试。Python3.15仅advisory。

最终构件在`.drov/t13-authorized-dist{311,314}/`，两解释器生成一致：wheel SHA256 **29e2e827c33e5ac85bd27506ce0c202a260ff94cc0a30006ff7d900d6ec8e2df**；sdist SHA256 **9aa12336759277ad0a95a71bdeed958ae8f0655e991c33467b2c486564ecbcdb**。比对记录`.drov/t13-authorized-artifacts.json`；最终补记未重新打包，不改变已验收的wheel运行载荷。

收尾按`dev.drov.execution`归属标签只读清点，本地Drov执行容器 **0**，`.drov/t13-container-inventory.json`；没有查询E2B或清理历史字节/台账。跟踪范围检查43个变更文件，凭据模式/被跟踪私有配置/品牌与P4和原三份DDL变更均0，`.drov/t13-track-check.json`。文档73份、相对链接613个、Python片段15个，错误0，`.drov/t13-doc-check.json`；AST检查不代表SDK执行。

**T-13整卡已完整验收并关闭，无剩余任务卡缺项。** 源码/测试/锁/工作流已提交推送，最终收尾仅Markdown，以skip ci保存，实际HEAD/同步以Git为准。单worker、POSIX文件层、已知长度原始上传、T-15维护与真实模型未验证等边界不变。

下一项 **T-14本地工作台**：复用同一HTTP/Runtime，交付会话/上下文/交互/取消重连、输入/代码/退出与清理状态、CSV/PNG安全预览/附件和wheel静态资产；三个参考应用、拒绝/失败/重连及安装后可用才关闭，具体状态与准备事项只维护在 [progress](../planning/progress.md)。建议New Chat，继续当前Local checkout；先核对26b97ee的两套最终CI及本节日志，不需要重开规划或重复无变化的全套测试。保留忽略的`.drov`/原环境、SQLite/bytes/租约/云账本和Docker；下一轮无需新仓库/账号/Key/model ID/预算，T-16真实smoke前再确认服务/固定ID/能力/费用上限并由宿主安全配置凭据。

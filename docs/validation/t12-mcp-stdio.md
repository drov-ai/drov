# T-12 MCP stdio 验证

日期：2026-10-15（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`，main / d7dadca，工作区干净。已读 AGENTS/progress/导航/handoff，核实 implementation-plan 的 T-10B 前置及 T-11/T-10C 完整验收。GitHub 实查763c25d的核心38683462374/Docker38683462346均completed/success；不重新启动一般规划。

## 本轮交付与环境

实现 [ADR-0024](../decisions/0024-mcp-stdio-tools.md)、[MCPStdio/MCPToolPolicy](../../src/drov/integrations/mcp.py)、core-safe MCPTool、Runtime资源绑定/固定注册、同一dispatcher的可选字段处理、[官方SDK真实测试服务](../../examples/mcp_stdio_fixture.py)、[普通/stream示例](../../examples/mcp_stdio_offline.py) 与独立安装检查。源API/运行/架构/能力/工程/质量/跨入口案例/许可同步，不修改公共DTO/schema、SQLite DDL、品牌、历史P4指纹或原私有环境/云台账。

本机macOS ARM64，Python3.11.9与3.14.3，uv0.13.0。mcp2.3.0、mcp-types2.3.0、jsonschema4.26.0及其MIT元数据实际安装检查；只收紧已有mcp声明到精确2.3.0，解析版本/hash不变。GitHub tag API实查v2.3.0→2118f14f8a19bc158d8a1cf90af58d85d187f849；安装的client/client.py、client/session.py、client/stdio.py及POSIX/Windows进程清理五份源码与固定上游逐字节相等，记录`.drov/t12-upstream-{tag,source}.json`。协议来源及设计/协议边界见ADR，不用main当固定发布版本。

所有MCP连接均为本机独立stdio子进程；使用合成value7，无模型API、私有MCP服务、凭据读取或云调用。固定initialize握手实际2025-11-25，不能写成覆盖2026现代发现协议或整个MCP。

## 实际检查点

- 前置全库（启用本地Docker配置，不启用E2B云配置）：**1001 passed /61 skipped，214.82s**，`.drov/t12-prerequisite.log`。59项真实E2B因没有本轮授权配置跳过，另外两项为Python版本适用项；本轮没有读`.env.local`或动用旧云预算。
- 初步MCP矩阵55项通过，追加进程树/关闭锁/新连接审批恢复/策略预算/factory/取消后61项通过，最终补task-required/缺可执行文件/不匹配绑定后 **64 passed，46.86s**，`.drov/t12-mcp311.log`。后续最终检查点见下方，不能把中间结果冒充最终全库。
- 真实普通/stream示例各显示两页发现、审批前0/批准后1调用、两个进程实际退出；模型是ScriptedModel。跨新连接/SQLite重开审批保持原schema/参数与单次调用；工具错误、无效text/JSON结果、sampling请求、断连与卡住分别走read失败或write未知核查，原始错误正文不进入操作记录。
- 进程树样本包含忽略SIGTERM的父/子进程，真实SDK升级终止后用OS查询确认退出；关闭延迟屏障使Runtime明确ShutdownIncomplete，另一个SQLite实例仍StorageInUse，释放屏障后重试aclose并成功重开。包装Task取消不代替清理完成证据。
- Ruff、严格mypy与生成schema检查点通过；完整最终静态/构建/平台结果待追加。本轮记录与progress在远程矩阵结束前保持进行中，不提前关闭T-12。

主要实际入口（具体日志由末节补齐）：

```sh
uv sync --locked --extra openai --extra e2b --extra mcp
python -m pytest tests/test_mcp_stdio.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t12-dist311
python scripts/check_installed_mcp.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t12-dist311/drov-0.1.0a0-py3-none-any.whl
```

## 失败修正与边界

首轮测试夹具调用了不存在的journal.list，随后局部变量覆盖helper；按实际get接口修正。Ruff检查Markdown示例格式、严格类型检查SDK的snake_case参数/可选模块也已修正；这些失败没有被记成通过。初始化无tools时，原owner错误会使后续aclose重复报初始化错误；改为在owner内部记录初始化失败，让外层stdio正常清理，只把仍未结束/未确认的清理作为ShutdownIncomplete。未启动的缺可执行文件明确归为配置失败。另补打开完成前is_open不得为true。收尾审查改为原始session分页，避免高层Client重复吸收分页导致输出schema隐式重新发现；结果codec改用不可变JSON模板重建，防止共享字典使变更校验失效，并补缺extra在进程启动前明确提示。最终专项增加到66项，以下方最终实际结果为准。

发现/运行使用两个独立进程，服务启动本身必须可安全预检。关闭故障连接也会中断同连接其他调用，不自动重连。旧协议中列工具与调用不是原子事务；每次重读防止观察到的schema变化，不冻结远端实现。输入schema仅支持API列明子集，structuredContent仅有限JSON对象/null，媒体与资源拒绝。stdout由官方SDK解析，分页/结果字节限制是解析后限制；不声称恶意服务的硬内存或守护进程沙箱隔离。borrowed正常关闭由宿主负责，但调用故障会中止连接。真实模型质量、HTTP/UI、公开发布/部署均未做。


最终负载检查点：两套全库与两套打包/安装并发时，3.11有两项MCP测试因测试配置1s启动期限过短，在discover/open阶段超时（不是预期RPC错误/坏输出路径），其余1065通过、61skip，`.drov/t12-accepted-full311.log`。修正为非超时案例采用产品默认10s，专门hang案例5s；发现错误案例不再允许TimeoutError冒充目标错误，超时案例额外检查真实start/list屏障。SDK运行代码/默认值不改。该失败检查点不能写成最终全库通过；修正后结果见后续记录。

## 最终本地与构件证据

- 完整extras收集 **1128项**，新增 **66项MCP**，`.drov/t12-collection.log`。3.14运行代码最终检查点 **1069 passed /59 skipped，309.42s**（含99真实Docker），`.drov/t12-accepted-full314.log`。上述测试期限修正后两解释器MCP均 **66 passed**：3.11 **63.64s**、3.14 **70.38s**，`.drov/t12-timeout-fixed311.log`、314.log。3.11修正后全库结果在下面追加；不把此前两项启动超时写成全库通过。59项skip全部为本轮未授权/未配置的真实E2B云例，3.11另有两项Python3.12+ API适用跳过。
- Ruff、格式（含Markdown Python片段）、严格mypy **136文件**、生成schema均通过，`.drov/t12-accepted-quality.log`。独立core-only环境实际没有mcp/jsonschema/openai/e2b，严格mypy136文件通过，`.drov/t12-core-quality.log`；不通过把可选依赖装入核心来修复类型检查。
- 两解释器各完成wheel/sdist构建、从sdist重建payload比对、两套全新非editable核心安装/依赖兼容/六核心示例普通与stream：`.drov/t12-accepted-package311.log`、314.log和对应dist目录（共四套核心安装）。核心导入与MCPStdio构造在禁止可选导入/副作用的审计下通过。两解释器独立mcp extra安装后的真实普通/stream例均通过：`.drov/t12-installed311.log`、314.log；每个例两页发现、审批前0/批准后1调用、两个进程退出，协议2025-11-25，real_model=false。
- 最终测试修正提交c786fdc仅改测试期限和验证文档；运行源码保持046bf6c。最终构件 `.drov/t12-head-dist/` wheel SHA256 **fd698fab19da44fb5f18d1bed6fb42624adc2eee0154413db9010d5d02bcb63c**，sdist **445bcac36f7550892f9695708fce4a4e00f772c728e15b550b472c8180c9f070**。最终wheel全部payload与前述四套核心/两套mcp安装的wheel相等，69个Drov文件逐字节等于当前src；Twine strict通过，`.drov/t12-head-{artifacts.json,build.log,twine.log}`、`t12-wheel-source.json`。本节及后续Markdown补记不在该sdist内，不将文档补记冒充旧构件内容。
- 源码046bf6c和测试修正c786fdc均已推送origin/main。最终远程验收目标为完整 **c786fdc6542d95d815649c759bc41520e0dc6915**，核心38713669518、Docker38713669476；目前进行中，不能用旧提交的CI关闭本卡。前一046bf6c的CI仅作为历史检查点。

最终本地3.11修正后完整回归 **1067 passed /61 skipped，271.00s**，含99项真实Docker，`.drov/t12-accepted-fixed-full311.log`；与3.14的1069+59skip对应同一1128项完整extras收集。测试期限修正后两解释器66项MCP均通过，运行源码无后续变更。


最终c786fdc远程Docker4 jobs全部success；核心Windows3.11前置在已有Memory测试 `test_best_effort_next_step_can_retrieve_after_skipped_call` 失败（719通过/258skip/1失败，MCP extra尚未安装），日志 `.drov/t12-win311-job.log`。第二次合法best-effort检索也可能超过该测试的10ms期限，不能要求必定included。仅修正测试：首读用Event等待实际超时取消，检索期限1s以保证下一步的成功断言不依赖Windows调度tick；仍检查首次timeout、次步included、两次实际检索、首模型无记忆/次模型含记忆。生产检索代码/默认值不改。修正后两解释器Memory回归与最终CI另记，不把该失败忽略或重跑碰运气当验收。

上述Memory测试修正后，完整Memory模块在3.11 **56 passed，5.80s**、3.14 **56 passed，6.09s**，`.drov/t12-memory-fixed311.log`、314.log。MCP运行源码及已有两套66项证据均未变化；新提交将重新核对完整远程核心/安装和Docker矩阵。

当前验收目标（待下节最终确认）：测试修正 **4ebedb782c9140be5e5a4024f27d0ac81a25e126**，已推送origin/main；[核心38714978847](https://github.com/drov-ai/drov/actions/runs/38714978847) / [Docker38714978829](https://github.com/drov-ai/drov/actions/runs/38714978829)。c786fdc核心随后因新提交取消，不能写为success；其Docker4套success仅是历史检查点。若中断，保留当前checkout与忽略台账，先核实这两个headSha/全部jobs和Windows3.11的Memory→MCP→安装流程，失败修复后再关闭整卡，不跳到T-13。

4ebedb7本机构件 `.drov/t12-accepted-head-dist/`：wheel仍为 **fd698fab19da44fb5f18d1bed6fb42624adc2eee0154413db9010d5d02bcb63c**，sdist **5cf58563c3b6bb983ecbe1b81ab579fc2b06ecb69b2879666d46f584c5035d6a**；wheel全部payload逐字节等于已做两解释器核心/extra安装的wheel，Twine strict通过，`.drov/t12-accepted-head-{artifacts.json,build.log,twine.log}`。新sdist含Memory测试修正，本段及最后验收文字是构件后的Markdown补记。没有新增发布动作。


Windows日志复核发现4ebedb7的job“success”不能作为验收：PowerShell多条native命令让前面失败被后面Responses安装的0退出码覆盖。Windows3.11实际 **52 failed /14 passed** MCP，安装例也失败；另有既存E2B安装probe左侧resolve长路径、右侧sys.prefix仍8.3短路径而断言失败。日志 `.drov/t12-win311-accepted.log`。已拆为独立CI步骤，并将真实MCP专项前置到构建前，避免旧false-green；E2Bprobe同时resolve两侧路径。MCP启动根因方向为覆盖SDK OS变量至空，违背Windows有效SystemRoot要求；恢复固定SDK OS白名单并保留模型Key/任意变量不继承，版本化环境策略进入MCP source_hash，API/ADR同轮同步。后续真实Windows通过前不宣称修复已验收；以前全部绿色Windows步骤记录作废，不关闭T-12。


OS环境修正dd73a0a后，真实MCP前置门禁在Windows3.14成功；Windows3.11为 **65 passed /1 failed**，失败是stubborn样本在SDK返回后立即查询一个父PID仍未signalled，`.drov/t12-os-env-win311-failed.log`。已改为5s内持续观察全部已记录父/子PID，必须实际消失，超时仍失败，不以SDK Task结束替代OS退出。依据微软 [TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess) 的异步终止/待完成I/O说明（查阅2026-10-15）；是否属于退出通知竞态由下一轮Windows真实测试确认，未直接认定已修复。生产关闭代码不因这一测试调整而改动。

系统环境最终修正后两套完整extras/Docker回归重新通过：3.11 **1067 passed /61 skipped，282.79s**，3.14 **1069 passed /59 skipped，304.67s**，`.drov/t12-env-final-full311.log`、314.log。MCP专项分别66项通过（62.71s/70.90s），5s OS退出观察测试本地66项也通过（62.17s）。两解释器再次完成构建/从sdist重建/四套core-only安装与普通/stream示例，`.drov/t12-platform-package311.log`、314.log；独立mcp安装后的真实stdio例和E2B离线安装probe均通过，`t12-platform-{mcp,e2b}-installed{311,314}.log`，real_cloud=false。

该批wheel SHA256 **0adf28f05a5cbb25962087a27bb74cb879587aee8d74235a221dfccf6525dd0c**，sdist **5128b0612d1904febee02be088d15590c4850ef338e84b0d65f7622225791800**；Twine strict通过，构件位于 `.drov/t12-platform-dist311/`、314/。这批构件含OS白名单修正，后续只改OS观察测试/验证文字，不将旧fd698构件冒充新环境语义。

3a013be 的独立MCP前置步骤在Windows3.11/3.14均已success，实际66项与5s父子PID退出核实通过，证明上一检查点的退出通知竞态已由真实OS观察解决；没有把测试改为只看Task状态或跳过Windows。最终目标是完整 **3a013be1e7c8668c98def8f8a1fd5350cf8e6a71**：[核心38718551848](https://github.com/drov-ai/drov/actions/runs/38718551848)、[Docker38718551936](https://github.com/drov-ai/drov/actions/runs/38718551936)，核心其余完整流程尚待最终确认。

## 最终验收与交接

**T-12整卡已完整验收并关闭。** 最终检查点为已推送的 **3a013be1e7c8668c98def8f8a1fd5350cf8e6a71**。再次实查确认 [核心38718551848](https://github.com/drov-ai/drov/actions/runs/38718551848) 与 [Docker38718551936](https://github.com/drov-ai/drov/actions/runs/38718551936) 的headSha均为该完整值，status=completed、conclusion=success，全部10+4 jobs均success。元数据 `.drov/t12-exit-observed-{core,docker}-ci.json`；逐job日志 `.drov/t12-ci-logs/`，实际测试汇总 `.drov/t12-ci-test-summary.json`。不采用旧4ebedb7的false-green或其它head代替。

| 最终远程范围 | 实际结果 |
| --- | --- |
| MCP独立前置 | Linux3.11–3.14、macOS3.11/3.14、Windows3.11/3.14，每套 **66 passed**，均无MCP跳过。Windows分别132.10s/103.29s，含5s内实查父/子PID退出、SDK/stream/原审批恢复/保锁 |
| 核心无extras | POSIX3.11：825+153skip，其余827+151skip；Windows3.11：720+258skip，3.14：722+256skip。MCP在该阶段按未安装extra跳过，不能代替上项真实连接证据 |
| 可选安装/协议 | 八套支持平台均完成核心wheel/sdist重建/非editable安装，独立mcp extra普通/stream真实服务及Responses本地HTTP例。Responses POSIX59、Windows57+2skip；E2B离线POSIX44、Windows37+7skip，安装probe真实通过且real_cloud=false；各native检查独立步骤，失败不能被后续0退出码覆盖 |
| Docker Linux3.11–3.14 | 每套 **99 passed /3 skipped /935 deselected**；3skip为未安装的可选模块，不是跳过99项Docker。每套还完成core-only安装后标准/分析镜像的实际SDK/stream执行/产物读取 |
| 质量/下一版Python | 严格静态/类型/schema通过；Python3.15 advisory也success，但不扩大3.11–3.14支持承诺 |

最终MCP运行源码为dd73a0a的OS白名单/环境策略修正，3a013be只补OS退出观察与验证文字。本地最终完整extras1128项中，3.11 **1067+61skip**、3.14 **1069+59skip** 均有系统环境修正后的实际全库证据；完整包/安装、69个Drov wheel文件与当前src逐字节相等及Twine证据以上述0adf28构件为准。后续收尾仅Markdown，不伪称旧sdist包含本节文字，不重跑未改SDK的矩阵。产品定义中遗留“沙箱未实现”措辞已去除，状态继续由progress唯一维护。

未读真实密钥、未调用付费模型或云、未访问私有MCP服务、未上传PyPI/公开镜像或部署站点。MCP仅支持当前API/ADR的initialize握手/schema/text与JSON子集；OS白名单是环境传递规则，不是宿主文件/网络沙箱。解析后限额也不等于原始stdout硬内存上限。没有未完成的T-12验收子范围；更广协议/schema/媒体/远程认证仍按能力表后续范围处理，不混记为本卡缺项或已支持。

下一项 **T-13 HTTP与身份边界**：DrovApp/lifespan、可信身份与Authorizer、同一Runtime的运行/交互/inspection/SSE、有界上传/同scope消费/文件授权下载，完成三参考场景及SDK/stream/HTTP对照、SSE背压/断连/关闭和撤权/跨归属/未ready/重复接收错误路径才关闭。现在不需新仓库、账号、模型ID、Key或预算；本地合成服务与身份夹具由执行者准备。保持Docker Desktop与原`.drov`/私有env、SQLite/bytes/租约/云模板和预算台账，T-16前才另行确认模型服务/固定ID/能力依据与费用上限，凭据仅宿主安全配置。建议 **New Chat**，继续当前Local checkout，先核对实际Git与本节两套最终CI。

收尾文档检查：**71份Markdown、587个相对链接、15个Python片段AST，0错误**；格式检查207文件、Git差异空白检查通过，修改范围凭据模式0命中，`.env`/`.env.local`/`.drov`仍忽略且未跟踪，无品牌/DDL/schema/已验收源码改动。证据 `.drov/t12-doc-check.json`、`t12-track-check.json`。最终收尾仅product/progress/本记录三份Markdown，以skip ci提交推送；实际最终HEAD/同步状态以Git为准。

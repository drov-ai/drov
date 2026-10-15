# T-14 本地工作台验证

日期：2026-10-15（Asia/Shanghai）。起点 `/Users/yanlong/projects/drov`，main / cf6c8cd；工作区干净，与 origin/main 同步。已读 AGENTS/progress/导航/handoff、T-14任务卡、API/运行/工程/质量及ADR-0025。实际查询前置26b97ee的核心38746896113与Docker38746896088，均completed/success、head_sha相符，分别10/4个job；元数据存于`.drov/t14-prerequisite-{core,docker}.json`。T-13无剩余验收缺项，没有重开一般规划。

## 实际交付与边界

按 [ADR-0026](../decisions/0026-local-workbench.md) 实现同源工作台、固定公开登录资产/认证数据、最小注册目录、会话/Run/上下文/事件、审批/输入/核查/取消/恢复及认证fetch重连。受控上传仍由原接收事务消费，文件经授权/hash读取，真实PNG才解码，其他文件仅附件。TypeScript类型来自真实OpenAPI，构建资源纳入统一wheel/sdist；core-only无需Node/Web导入。原Runtime/DTO/DDL/uv.lock/P4指纹和忽略品牌原素材不变；HTTP新增AgentCatalog、无损会话查询入口和DrovApp.workbench装配选项，源契约/案例/质量/归属同轮同步。

本机macOS ARM64，Python3.11.9/3.14.3、uv0.13.0、Node24.14.0/npm11.9.0。实际精确npm锁：Vite8.3.4、TypeScript5.9.3、openapi-typescript7.13.0、Playwright1.64.1、Prettier3.8.3；浏览器Chromium156.0.8078.4（Playwright build1248）。没有读取真实Key/私有env、付费模型或云调用，没有公开发布包/镜像/站点或部署。测试文件、身份、模型输出全部合成；真实Docker复用已准备的本机Engine/profile，不拉取/构建新镜像。

## 失败、修正和中间检查点

- npm首次解析TypeScript7.0.2被openapi-typescript7.13.0的peer `^5.x`拒绝；改为实际兼容的5.9.3，未使用force/legacy-peer。Prettier3.9.6两次下载遭ECONNRESET，最终从本机缓存安装并锁定3.8.3；不把失败下载当已安装。
- 初次浏览器fixture在macOS的符号链接临时祖先目录被LocalArtifacts正确拒绝，改为解析真实路径；不放宽生产字节层。其后回归恢复fixture最初注入的失败未进入重启扫描，不能期待当前运行自动recover；改为在已有final保存后显式形成recovery检查点，页面只验显式恢复/原结果不重调。真实硬崩溃矩阵仍由原T-10/T-10B覆盖。
- 首轮可用浏览器矩阵8通过/4失败/1无Docker配置skip（`.drov/t14-ui-second.log`）。修正开始后重复loadRun导致订阅被自身取消、撤权开关/错误信息未同步、文件描述以user消息追加使脚本例误判断当前阶段。三参考应用、写入/拒绝/核查不变；没有改内核来迁就UI。
- 启用真实Docker后的初轮12通过/1失败，发现暂存后重建表单把已选择Agent重置为第一项，原服务正确拒绝跨目标消费。保留选中值后，真实上传/审批/执行/结果已成功，但自动测试对异步按钮移除的while循环产生竞态；改为逐文件等待真实信息响应，未延长超时掩盖失败。日志`.drov/t14-ui-third.log`、fourth.log保留。
- 修正后13项浏览器全通过，含真实Docker（11.9s，`.drov/t14-ui-fifth.log`）：实际上传numbers.csv、批准原代码、生成25字节summary.csv与120×80 PNG、发布后active lease=0、CSV实际内容/hash、PNG解码/预览/下载，查看前后模型调用计数不变。桌面/390px视口截图实际已打开检查，无横向溢出。后续补3项协议/字节检查，无Docker的16项运行15通过/1明确skip（9.6s，`.drov/t14-ui-sixth.log`），不把这次skip冒充Docker成功。
- Python原HTTP62项通过（3.98s，`.drov/t14-http-first.log`）；新增5项公开资源/认证边界与原HTTP合计67通过（4.62s，`.drov/t14-http-static.log`）。本地真实HTTP资产探针通过（`.drov/t14-local-assets.log`）。Ruff、228文件格式、155文件严格mypy、schema/OpenAPI检查已通过；最终构件/CI另记，不以中间结果关闭整卡。

补充接口审查发现合法会话ID可含斜杠/点段，原路径入口不能完整表示；新增查询形式与原读取共用同一函数/授权，不改SDK标识范围。新增5项先得到实际404失败（`.drov/t14-session-before.log`），接通后原HTTP+资源/查询合计**72通过（5.02s）**，`.drov/t14-http-query311.log`。页面改用查询形式，并补端到端往返。

## 验证入口

```sh
npm --prefix workbench ci
npm --prefix workbench run schema
npm --prefix workbench run format:check
npm --prefix workbench run build
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" npm --prefix workbench test
python -m pytest tests/test_workbench.py tests/test_http.py tests/test_http_transport.py tests/test_http_artifacts.py tests/test_http_scenarios.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t14-dist311
python scripts/check_installed_server.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t14-dist311/drov-0.1.0a0-py3-none-any.whl
```

## 验收范围及未验证

工作台为实验性本地开发界面。浏览器只验Chromium及移动尺寸视口，不宣称Firefox/WebKit、真实手机/读屏器或外部用户体验通过；不改变Python既有支持矩阵。原单worker/Host-Origin/Bearer、POSIX文件层、已知长度原始上传和T-15暂存维护限制保留。没有E2B跨HTTP/UI声明，未动用其历史云预算。真实模型质量和费用仍留T-16；独立业务副作用恢复/硬崩溃的原矩阵没有以UI合成暂停替代。

正文默认关闭；空闲页面不即时感知宿主撤权，下一次同步/读取会清除正文/文件名/Blob并丢弃旧视图响应；已经下载的字节无法撤回。UI只展示最近1000个事件、暂时输出有界，完整日志留在原存储；它不是持久导出、离线维护或Notebook。代码结果未发布时，UI只说明尚无已发布销毁证据，不推断已清理。品牌选定SVG为逐字本地副本，其他品牌原素材未改；初次公开包/站点前仍需来源/公开使用范围整理。

## 最终结果

最终代码检查点（提交/CI另补）：**23项Playwright全部通过（17.6s）**，其中4项协议/字节、19项浏览器流程，真实Docker必跑（DROV_REQUIRE_DOCKER=1），`.drov/t14-ui-complete311.log`。Docker用例还在已预览后撤销view_content，后续下载拒绝并清除文件名/图片；核查/取消/拒绝/查看均有真实计数断言。补测输入草稿/焦点在同Run补读时保留，第一次测试误把形成暂停所需的SSE转移也阻住而超时，改为先等待实际暂停后触发补读（原日志`.drov/t14-ui-accepted311.log`），没有改内核或延长期限。

Python历史全库检查点（查询入口前）3.11为**1137通过/61skip，305.86s**（`.drov/t14-full311.log`）；不得把该结果当新增查询入口的整库结果。新增5项查询和原HTTP/资源已**72通过，5.02s**，最终全库/构件/平台另补。当前状态与未完成项统一见 [progress](../planning/progress.md)。


### d2914ca 构件与安装检查点

源码/测试/锁/工作流已提交推送 **d2914caf942043a01b5cfc118eaed536b85b0018**；后续收尾只补Markdown。该提交的两解释器wheel/sdist、sdist重建载荷比对、四套core-only安装及六例普通/stream通过，`.drov/t14-d2914ca-package{311,314}.log`。两套独立server安装后，三场景实际HTTP/SSE和全部5个工作台资源/认证边界通过，`.drov/t14-d2914ca-installed{311,314}.log`；安装用户无需Node。Twine strict通过，`.drov/t14-d2914ca-twine.log`。

两解释器构件逐字一致，wheel SHA256 **051439d08f00904a5c29230286df255d0456be7476335919ea439d1d3f9dce64**，sdist SHA256 **7f676bd6ccc63df38e1d60554d58fcf9919926136d572d540cecff9cd82404f9**。wheel中全部**81个Git跟踪的Drov源码/资源文件**与该提交逐字节相等，`.drov/t14-d2914ca-artifacts.json`。首次载荷辅助脚本误纳入本机忽略的.DS_Store而报KeyError，改为Git跟踪集合后比对0差异；没有把OS元文件打包或忽略实际源码差异。构建之后补记不伪称已包含在该sdist。

含extras与core-only严格mypy各155文件通过，Ruff/231文件格式、生成schema/OpenAPI、文档74份/相对链接626个/15个Python片段AST、差异空白与凭据模式检查通过。凭据模式、uv.lock/原DDL/P4/品牌原素材变更均0，选定品牌副本SHA256为cc60756d84916ea18118eae48a3b78ca8e0145d6da94fdbb0b96afe07464b9ad；`.drov/t14-doc-check.json`。AST不代表SDK可运行；运行证据以上述实际示例为准。

最终Python3.14 HTTP/静态/查询专项**72通过（4.58s）**，`.drov/t14-http-query314.log`，OpenAPI与3.11相同。3.14实际浏览器同一最终静态资产**22通过/1无Docker配置适用skip（14.7s）**，`.drov/t14-d2914ca-ui314.log`；真实Docker浏览器由3.11的23全通过覆盖。明确复现原a/b会话被start接收202、路径读取404、查询读取200且ID完整，`.drov/t14-session-path-repro.json`；原入口保留，查询形式支持完整SDK合法ID，不修改标识符契约。

远程 [核心38758057652](https://github.com/drov-ai/drov/actions/runs/38758057652) 与 [Docker38758057752](https://github.com/drov-ai/drov/actions/runs/38758057752) 已对应该SHA启动。前端锁安装/类型生成/格式/构建后Git资源比较和无Docker浏览器job已success；其他job最终结论及本地最终全库稍后补记。不能以这一步的success关闭整卡。


示例收尾审查另发现FileDemoModel的单次FixtureModel会在带历史的会话中取第一个旧tool结果。新增纯协议回归先实际失败（`.drov/t14-example-history-before.log`），随后只把例子的输入限定到当前轮次，原请求不变；SDK/HTTP/工作台运行源码与静态资产不变。修正后协议/HTTP专项**73通过**（`.drov/t14-example-history-fixed311.log`）；实际make_app三场景及同一会话连续两次分析镜像CSV→400×300 PNG全部通过，最终输出引用必须与本次实际工具产物一致、active lease=0，`.drov/t14-demo-smoke-repeated.log`/json。此例子修正需另存源码检查点并核对其构件/CI，不能把d2914ca整套证据冒充更新后的提交。


最终例子修正源码为 **06455ff674358b491ac85d93e72500c15dd2787e**。其两解释器wheel/sdist与重建/四套core安装通过（`.drov/t14-06455ff-package{311,314}.log`），wheel继续是前述051439d0…字节，全部81文件与最终src相等；与已实测两套server安装的wheel逐字节相同。新sdist SHA256为 **496501824f70f0f568291511430ad655dc85ee36a81b98e69ed2d7fab431e9f5**，两解释器一致；`.drov/t14-06455ff-artifacts.json`。最终Twine strict通过。严格mypy（含extras/core-only）各156文件通过，Ruff/232文件格式通过。

最终3.14实际浏览器**23全通过，含真实Docker（15.9s）**，`.drov/t14-06455ff-ui314.log`；make_app的同会话连续两次真实分析镜像CSV/PNG及引用绑定通过。3.14协议/HTTP专项73通过（4.40s）。同一Runtime/静态资产的3.11全库检查点d2914ca为**1142通过/61skip，297.00s**，`.drov/t14-d2914ca-full311.log`；这之后仅例子修正和新增1协议回归，3.11专项73通过（5.15s），未将旧1142伪称新1204项整库。最终3.14全库与远程[核心38759707490](https://github.com/drov-ai/drov/actions/runs/38759707490)/[Docker38759707538](https://github.com/drov-ai/drov/actions/runs/38759707538)另补。旧d2914ca的Docker4 jobs成功，核心因推送本次例子修正而取消，不能作为最终整套通过。


最终源码06455ff本地Python3.14含真实Docker全库**1145通过/59skip，307.05s**（完整收集1204，`.drov/t14-06455ff-full314.log`）。59项仅为未配置/未授权的真实E2B；没有读取私有env或动用云预算。只读清点本地Drov执行归属标签，剩余容器**0**，`.drov/t14-container-inventory.json`。

远程06455ff首轮Docker在3.12的首个HTTP-Docker SDK用例、Runtime.open查询`docker system info`时触发原10s控制期限；**101通过/1失败**，尚未创建Run/执行代码。其余3个Docker job成功；原失败完整保留`.drov/t14-06455ff-docker-failed.log`与attempt1元数据。未改生产控制期限、测试断言或跳过项，只对3.12在新runner重跑（同一run的attempt2），结果另补；不能把“新runner”推测写成已诊断的OS耗时根因。


收尾同页身份审查：原“跨归属”浏览器helper重新导航整页，未覆盖同页注销后的控件复用。改为同页切换后，实际复现旧Session表单值保留，以及已发出的verify回执在切换身份后短暂写入旧核查证据；MutationObserver捕获到旧证据（`.drov/t14-identity-before.log`，2失败），没有以随后错误覆盖/最终截图不见作为通过。修正注销清空所有表单/游标/缓存DOM、控制/核查回执绑定原视图代次、文件固定原Client/AbortSignal，并在隐藏正文时清除提示区证据。4项身份/交互专项通过（`.drov/t14-identity-fixed.log`）；最终全矩阵与构件需按新的静态资产另补。Python/Runtime/HTTP/DDL源码没有改变，06455ff的1145项全库保持为同一Python实现证据，不能把旧23项UI/旧构件冒充新24项UI与新资源。


06455ff的最终核心11 jobs全部success，Docker attempt2的3.12重跑也success，其他三个首轮已成功；该源码的15个job均有完成证据（`.drov/t14-06455ff-core-ci.json`、`t14-06455ff-docker-retry.json`）。这支持原Python/HTTP实现与23项UI的检查点，不用于替代后续同页身份修正的新静态资源。新24项初轮为23通过/1测试失败：关闭正文后提示区已被清空并hidden，测试用默认可见role定位导致未找到元素；改为检查保留的DOM节点文本确实为空，未放宽“不含旧证据”的要求（`.drov/t14-final-identity311.log`）。

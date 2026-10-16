# T-15 数据维护与兼容验证

日期：2026-10-16（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`，main/213ab8b，干净且与 origin/main 同步。按 AGENTS/progress/导航/handoff、T-15 卡及维护/运行契约实施，没有重新规划。实际查询前置最终源码5228c57743e23554ff9e5a32e0ec4525bc93944c：核心38763139995的11个job、Docker38763139909的4个job均completed/success；原完整元数据存 `.drov/t15-prerequisite-{core,docker}.json`。前置存储/工作台42项通过（0.80s），`.drov/t15-prerequisite-tests.log`。

## 本批交付与限制

按 [ADR-0027](../decisions/0027-offline-session-retention.md)，交付可信本地离线 SQLiteMaintenance/CLI、无正文预览、同事务 Session 逻辑清理/最小墓碑、去重/归属与当前 read、未知操作/租约/Memory scope 保护、共享文件最小来源证明及 E2B 累计预留保留。配对库必须取得原字节锁，不创建新库或配对；无 HTTP 管理路由，无默认清理。

**这是 T-15 子范围。** `bytes_reclaimed=0`，stage.json/真实字节和容量仍保留；未交付字节/暂存回收台账、data_abandoned 管理动作、SQLite+blob 一致备份/恢复/barrier、实际记录迁移、正文/文件导出。不能以本批 SQL 原子删除或旧版本拒绝替代这些完成条件。任务状态与下一批步骤统一见 [progress](../planning/progress.md)。

原 core records/user_version/record_schema 及 Artifact/Sandbox/Upload DDL/版本、uv.lock、品牌原素材和 P4 历史指纹未改。新增 retention 扩展参考/SessionDeletionPlan schema 来自实际定义；原库仅预览不启用扩展。没有读取真实 Key/私有 env、模型/云调用、私有资料上传、公开包/镜像/站点发布或部署。原 `.drov` 的用户数据与预算/租约台账没有作为破坏性夹具；实际测试全部使用临时合成库/文件，Docker复用已有固定镜像。

## 实际失败与修正

- 首次21项均因新增事务检测在新库建 metadata 之前查询该表而失败（`.drov/t15-maintenance-first.log`）；改为初始化期间沿已有版本判断，正常生命周期每个新事务刷新 retention 扩展，避免取消后遗失提交回执的实例状态过期。
- 第二次19失败/2通过（`.drov/t15-maintenance-second.log`）：严格 JSON 不接受 SQLite tuple，快照先转为 JSON list；测试 Memory CAS 原来误用None，按实际API改为0。第三次21通过（1.20s）。没有放宽生产 JSON/CAS 规则。
- 加入进程硬终止/租约/费用后26通过（1.56s，`.drov/t15-maintenance-fourth.log`）。随后31项首跑30通过/1失败：HTTP夹具缺 application/octet-stream，被原415规则正确拒绝（`.drov/t15-maintenance-fifth.log`）；补正确请求头后31通过（1.91s，sixth.log）。没有修改上传协议以迁就测试。
- 新示例先暴露实际 ModelResponse 必需 finish_reason、ScriptedStep 构造参数不符；按真实模型夹具协议改为直接 ModelResponse。修正后SDK与stream实际各执行一次模型夹具，清理后旧请求ContentGone，调用计数仍1。
- 严格类型/格式检查发现可空阶段ID、测试CAS和类型注解、局部变量类型复用及异步夹具阻塞写入等问题，已修复。完整mypy覆盖scripts后为162文件通过；局部扫描单独依赖旧测试fixture时曾缺`_e2b_host`搜索上下文，不把该局部错误当SDK缺模块。最终检查另记。

## 已运行检查点

- 存储/文件/E2B离线/HTTP/工作台相关127项通过（4.21s），`.drov/t15-core-regression-first.log`；不是全库。
- 扩展原三入口真实Docker用例：SDK/stream/HTTP各生成CSV和PNG、销毁后重开下载，再显式跨Session共享两个产物，删除原Session后继续授权/hash读取，清理最后引用后返回ContentGone，原模型计数仍2。三项通过（4.33s），`.drov/t15-real-docker-first.log`。没有云调用、模型生成质量或物理回收声明。
- Ruff、全库严格mypy162文件、生成schema/OpenAPI检查通过；完整源码最后检查点的全库/安装/平台结果在下文补记。未跑项目不因这里出现通过字样而默认覆盖。

## 可重复入口

```sh
python -m pytest tests/test_maintenance.py tests/test_maintenance_http.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" DROV_REQUIRE_DOCKER=1 python -m pytest tests/test_http_docker.py -q
python examples/maintenance_offline.py
python examples/maintenance_offline.py --stream
python -m drov.storage.maintenance --help
python scripts/generate_schemas.py --check
python scripts/generate_openapi.py --check
```

首个命令的HTTP部分需server extra，POSIX字节子范围在其他平台显式跳过。CLI只管理调用者指定的现有库，本轮不会对真实用户库运行删除。

## 收尾审查与精确检查点

- 1176通过/61skip（298.47s，1237项）的Python3.11含真实Docker全库是历史检查点：在新增cursor_expired映射之前，日志`.drov/t15-full311-first.log`。映射按原API契约修正后，相关SDK/HTTP/流专项154通过/1适用skip（8.03s，`.drov/t15-cursor-final311.log`），不能把早先全库写成含该修正的最终全跑。
- 原核心源码f8b5f1b检查点的Python3.14含真实Docker全库1178通过/59skip（326.60s，1237项），`.drov/t15-full314-final.log`。此后CLI增加摘要格式拒绝及SDK游标断言，8b5914a两解释器维护专项各33通过（3.48/3.59s）；CLI不把非法/非ASCII摘要变成TypeError traceback。原3.14环境未装server extra的一次检查31通过/1模块skip（`.drov/t15-maintenance314-first.log`）已保留，随后按原uv.lock实际新建全extras环境复跑，不把skip当HTTP成功。
- 历史wheel最初误取通用t14-dist311目录，83前的源码比对在routes.py处拒绝；改取验证记录指定的`t14-5228c57-dist311`。其81个SDK/资源文件逐字等于5228c57，wheel SHA256为55fd0fda20b2952f2358355a9bc9bae12d34dac665966566b11cf80b1b829e89；隔离进程实际从该wheel载入旧SDK，接受原库、拒绝retention扩展库，两个数据库字节均不变（`.drov/t15-old-compat-final.log`）。这是仓库历史开发wheel的兼容检查，不是虚构已公开发布版本或真实旧记录迁移。
- 收尾再发现现有空文件会沿普通Storage.open初始化、缺少artifact_store_id时原字节装配会重新配对；维护必须在这些写入之前拒绝。18dfbcb增加私有existing-only打开通道和配对完整性预检，普通SDK初始化行为保持。新增2个真实异常库回归，源码/SQL/源语义按既定只预览规则修正，没有自动迁移或修复。两解释器存储/实际进程/维护/HTTP专项各70通过（4.39/4.43s，`.drov/t15-readonly-accepted{311,314}.log`）；SQLite自身故障/Runtime故障各40通过（4.25/5.05s）；最终三入口真实Docker回归3通过（4.63s），`.drov/t15-readonly-docker311.log`。
- 上述专项首次误指定不存在的test_storage_faults.py，两个命令均exit4/no tests ran（`.drov/t15-readonly-final{311,314}.log`）；按实际test_storage_processes/test_sqlite_boundaries/test_runtime_faults重跑的结果才是上述70/40，不将空收集算通过。
- 8b5914a历史两解释器wheel/sdist重建、四套core-only安装及七例SDK/stream、两套独立server安装后的实际三场景HTTP/SSE和5个工作台资产均通过；83个SDK/资源文件逐字一致，Twine strict通过。源码18dfbcb之后的最终构件另验，不将旧构件冒充新维护打开通道。

该打开保护检查点为18dfbcb01f682ef89abfe5327ce2bd3a35aeed5e（已推送）；其本地全库、构件和远程平台结果见对应下文，后续d70576a/9fd4170检查点不能被此项替代。旧8b5914a触发的CI/Docker为38831359680/38831359672，新提交的CI/Docker为38831850378/38831850400；只以新SHA最终结果验收本批。较早检查点无论success/cancelled都不替代新源码验证。

## 18dfbcb 本地验收检查点（历史）

本机Darwin ARM64，Python3.11.9/3.14.3，uv0.13.0；3.14全extras环境按原uv.lock创建，没有更改锁。最终3.11含真实Docker全库 **1178通过/61skip（301.80s，1239项）**，`.drov/t15-accepted-full311.log`。59个skip为没有本轮授权的E2B远端测试，另2个为3.12+ API；未启用云测试不能据此声称远端支持发生新变化。原3.14的1237项全库对应前述更早检查点，最新打开保护由两解释器各70+40专项、3条实际Docker和最终构件验证，不冒充新增2项后的3.14本地全库重跑。

新增 **35项Python**（33维护/真实SQLite进程，2HTTP含文件/POSIX子范围），另扩展原3个实际Docker用例；没有以重命名测试重复计为新增。默认预览、现有空文件/缺配对拒绝、无默认初始化、当前授权、旧scope/请求保护和已清理cursor_expired均在最终专项/全库内实际验证。

最终两解释器wheel/sdist重建、四套core-only安装与七例SDK/stream、两套独立server安装后的三参考场景实际loopback HTTP/SSE和5个工作台资产、Twine strict全部通过。日志`.drov/t15-readonly-{package,installed-server}{311,314}.log`和`t15-readonly-twine.log`；构件/所有83个SDK及资源文件与18dfbcb逐字一致，记录`.drov/t15-readonly-artifacts.json`。两套wheel SHA256 **8bb9bd357f95574b95e653b89c5b52785787bafc47f649b70130a5d99187e31b**，sdist SHA256 **54e1b3b5c83ab1d6e44ec1fd5a089c1f0a12aab2bcded0a7e74b268703502743**，两解释器相同。此后的收尾仅Markdown，不将补记伪称已含在这些sdist中。

严格类型162文件、Ruff/240文件格式、生成schema/OpenAPI和Git空白检查通过；原core DDL实际AST常量逐字相同，记录对象/Artifact/Sandbox/Upload定义、uv.lock、历史P4和品牌原素材无变更。文档78份/相对链接668个/Python片段16段AST检查无错误（AST不等于SDK可运行）；收尾追加后再复核。

## d70576a 归属隔离修正

收尾用实际SDK复现：其他user、tenant或Agent在自己的输入中仅提到原Artifact ID，没有授予文件，原全局正文扫描却使源Session清理后文件仍ready。新增3项回归在修正前全部失败（`.drov/t15-scope-mention-before.log`）。修正限定同tenant/user/Agent内的保守正文引用；跨作用域只认可实际grant/类型化租约输入或原上传快照，不把其他scope的普通文本或后端配置字符串当文件引用。API/运行契约/ADR同轮同步，不扩大默认跨用户/Agent复用权限。

最新源码 **d70576a76dcbc5bb911abc7e514262cc92bc5462** 已推送。两解释器维护/HTTP/文件输入专项各72通过（5.79/5.89s，`.drov/t15-scope-mention-accepted{311,314}.log`），三入口实际Docker再验3通过（4.74s，`.drov/t15-scope-mention-docker.log`）。累计新增38项Python（36维护/进程/归属，2HTTP）；3个Docker原用例扩展不重复算新增。共享文件实际检查是同tenant/user/Agent下跨Session的持久引用，未把自定义跨用户/Agent授权回收策略写成已验收范围。

两解释器此源码的wheel/sdist重建、四套core-only安装和七例SDK/stream全部通过，`.drov/t15-scope-package{311,314}.log`；83个SDK/资源文件逐字等于d70576a，`.drov/t15-scope-artifacts.json`。两套wheel SHA256 **8acec190764151f32df32907417ffbe364e5e5fddc29986aecf1c634ab8599f8**，sdist SHA256 **4a93e54a7e427f09c000df8cb78450d01e8543a4dccdb9216b894bf62b7a05b8**；Twine strict通过。独立server安装/最终全库/远程CI另补，不用18dfbcb的旧构件替代新引用规则。

## Windows 3.14 停滞的真实原因与夹具修正

d70576a的核心38832959068首轮10个job已success，Windows3.14长时间停在pytest，公开日志在进行中不可下载（API404，未登录页面要求登录；没有读取/传递凭据）。只在明显超过历史时长后请求取消以取得日志；完整原记录保留于`.drov/t15-windows314-cancelled.log`、`t15-core-attempt1-pre-cancel.json`。取消后发现pytest在01:14:56 UTC已输出 **1失败/752通过/269skip（477.83s）**，其后进程未退出直到01:24取消；不能把这次标成基础设施问题或通过。

实际失败为已有`test_cancelled_actual_thread_retains_slot_and_sqlite_lock`的finally里私设1s通知等待超时，导致后面的close_released_runtime被跳过，留下aiosqlite连接/线程；loop关闭还取消未结算的kernel事务。产品初始ShutdownIncomplete/20ms关闭宽限与存储锁断言不改。修复清理夹具，释放障碍后直接使用仓库已有、总期限5s的close_released_runtime重试同一次受控关闭；另一个同类Memory模型夹具也移除该重复通知等待。

新增1.1s真实线程完成通知延迟的确定性回归：旧finally实际在1.41s失败并不退出，父进程12s杀死仅含合成库的测试进程（`.drov/t15-thread-cleanup-before.log`）；改后两解释器Agent/运行所有权/维护/HTTP共 **100通过/2适用skip（6.04s）** 和 **102通过（5.97s）**，`.drov/t15-thread-cleanup-fixed{311,314}.log`。仍要求原线程确已派发、取消结果持久化、短宽限内关闭失败、槽位/SQLite锁保留、实际完成后释放、late-result未写入；新增case还必须实际收到延迟通知。不是放宽运行时预算、跳过断言或仅换runner把失败抹掉。

该修正仅改测试夹具，SDK/资源与d70576a逐字相同；累计新增39项Python（38维护/归属与HTTP，加1延迟通知回归）。最终Git/CI检查点另补。

## 9fd4170 最终测试检查点

最终测试修正 **9fd41701beb31b273bccda981d79e136c2d6b27e** 已推送。其SDK源码/资源与d70576a的Git差异为0；新增延迟通知case后，Python3.14本地含真实Docker全库 **1184通过/59skip（309.58s，1243项）**，`.drov/t15-final-full314.log`。Python3.11本地实际证据是18dfbcb全库1178/61与后续72项/100项专项，准确边界见上文，最终支持矩阵由新提交的远程检查证明。

9fd4170重新构建wheel/sdist并从sdist重建，所有83个SDK/资源载荷等于当前源码且两种来源wheel逐字一致（`.drov/t15-9fd-{build,rebuild}.log`、`t15-9fd-artifacts.json`）。wheel仍为同一SDK的 **8acec190764151f32df32907417ffbe364e5e5fddc29986aecf1c634ab8599f8**；含新测试/当时文档的sdist为 **9ed5e0e1591258367aca5bf2e6c37ec57d3f7240ca9b3b30d81120cb28dcfcb4**，Twine strict通过。d70576a的四套core-only/两套server安装证据对应相同SDK载荷；不将后续补记伪称已包含在这些sdist。

## 最终远程验收与恢复边界

实际下载最终元数据和完整日志，核对两个workflow的headSha均为 **9fd41701beb31b273bccda981d79e136c2d6b27e**：[核心38835428137](https://github.com/drov-ai/drov/actions/runs/38835428137)全部11个job、[Docker38835428057](https://github.com/drov-ai/drov/actions/runs/38835428057)全部4个job均completed/success。证据为`.drov/t15-9fd-{core,docker}-ci.{json,log}`；较早d70576a的取消/实际失败不作为通过，也未从记录中移除。

| 最终远程范围 | 实际结果 |
| --- | --- |
| Linux3.11、macOS3.11核心 | 每套863通过/160skip；另跑server74、MCP66、Responses离线59、E2B离线44，均通过 |
| Linux3.12/3.13/3.14、macOS3.14核心 | 每套865通过/158skip；各平台上述extras专项均通过 |
| Windows3.11/3.14核心 | 分别752通过/271skip、754通过/269skip；各自server60通过/14适用skip、MCP66、Responses离线57通过/2skip、E2B离线37通过/7skip；测试进程正常退出，后续构件/安装步骤全部通过 |
| Python3.15 advisory | 核心865通过/158skip，job success；保持advisory定位 |
| Linux原生Docker3.11–3.14 | 每套102通过/3适用skip；其中3.11的实际Docker工作台浏览器24项全通过，核心工作台23通过/1实际Docker适用skip |
| 构建与安装 | 各支持平台的wheel/sdist重建、核心干净安装、示例SDK/stream及extras隔离导入步骤成功；Windows按既定POSIX范围省略字节示例，不推断其字节层支持 |

可选依赖与真实Docker由独立步骤/专项执行，不能将核心pytest中的skip写成对应功能成功。E2B远端59项本轮没有运行；未调用真实模型、付费云、发布或部署。最终SDK载荷也在两套独立server安装后完成三参考场景真实loopback HTTP/SSE及5个工作台资产检查（`.drov/t15-scope-installed{311,314}.log`）。

**本批验收的是离线预览和数据库逻辑清理，不关闭T-15。** 字节、stage.json及容量仍保留；物理/暂存回收中断台账、data_abandoned、一致备份/恢复/barrier、实际迁移与授权导出未验收。恢复时从同一checkout按progress的下一批步骤推进，使用新的临时合成数据库/字节夹具；不得对原用户库重新执行删除。收尾仅补Markdown并复核链接/片段/空白，不改变已验证的源码/测试或声称重做全部矩阵。

收尾实查78份Markdown、668个相对链接、16段Python片段AST均无错误；对213ab8b以来跟踪差异的可识别凭据模式计数0，受保护原素材/锁/历史SQL/基线无变更，工作台品牌副本与原SVG逐字相同（`.drov/t15-doc-check.json`）。Git空白检查通过；该文档轮不将AST/模式扫描等同运行或安全保证。进度、记录与CLI摘要占位符补记以`[skip ci]`提交推送，实际HEAD/同步以Git为准；全部运行证据仍绑定9fd4170。

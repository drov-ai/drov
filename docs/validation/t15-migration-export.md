# T-15 收尾：历史记录迁移与授权导出

日期：2026-10-16（Asia/Shanghai）。同一Local `/Users/yanlong/projects/drov` 接续；前四批已验收。第四批原992037d在Windows3.14跨stat/fstat的ctime比较失败，修正a83fa79已推送，本地全库1292通过/59skip和构件/安装通过；原生Windows HTTP两版本已成功，最终精确SHA核心11/Docker4 jobs全部success，首次浏览器超时同SHA复验通过，原失败保留；详细状态见[备份记录](t15-backup.md)。T-15未完整验收前不进入T-16。

## 范围

[ADR-0031](../decisions/0031-compatible-v1-record-migration.md)实际v1兼容字段回填、完整备份与原子不可变回执，[ADR-0032](../decisions/0032-authorized-portable-exports.md)默认元数据/正文与逐文件授权/JSON副本redactor/有界分页导出。原DDL/版本保持，迁移独立migration_schema=1。补充恢复屏障对cancel和新data_abandoned的保护，旧prepared不能因取消/放弃被错误写成“备份之后未执行”；纯观察、原回执查询、同义迁移和成对备份可用。没有通用自动/force解除屏障或应用专用外部事实重建工具。

所有破坏性与错误/硬终止只用临时合成库/文件，复用原本地Docker；没有读取真实Key/私有env、使用付费模型或E2B、改原用户库/字节/租约/云台账/品牌，未公开发布或部署。

## 历史与实际修正

- 从历史d84c66fb7df36395d679c1dd4b45a99e5bbc23f1构建开发wheel，29 SDK载荷逐字对应Git，SHA256 **3b95f09fdc40e93b0a4498ae1306cdb4157f063dd251b647fbdb3fd05ae18f58**；实际隔离载入旧SDK生成[合成历史夹具](../../tests/data/storage-t05-v1.json)。首次误用旧版本不存在的run.started枚举而未生成成功，改实际run.running后成功，`.drov/t15-t05-fixture-generation{,-second}.log`。它不是已发布v0、真实业务写入或真实模型证据。
- 迁移首轮8项因夹具INSERT误写11个占位而原表实际10列全部失败（`.drov/t15-migration-first.log`）；修正夹具后8项通过（0.54s，second.log）。增加4个实际进程kill/SQL故障/丢应答/取消等后，迁移与备份52项通过（5.21s，third.log）；Windows兼容修复期间先stash保存本轮WIP，之后在a83fa79安全恢复，含新增2回归共54项通过（5.00s，restored.log），没有丢工作或跳过第四批失败。
- 导出首轮12失败/1通过（1.35s，`.drov/t15-export-first.log`）：原RunInspection按契约只嵌入上下文摘要，不能放完整正文；导出增加独立context_manifests字段，原检查/查询语义不改，第二轮13项通过（1.10s）。扩展逐文件/未知事件/控制事实后15项通过（1.47s，third.log）。逻辑执行键/预览回归首次使用带自定义validator的SandboxResult作普通Python工具结果被既有schema边界拒绝，改普通dataclass夹具，不放宽工具契约；随后16项通过（1.37s，fifth.log）。
- 三入口实际Docker导出初次3项失败（4.10s，`.drov/t15-export-docker-first.log`）：工具派发事件顶层execution_key未投影。补充已知事件控制字段/结构化结果/已知标识文本、截断预览投影及不可任意删除业务directory字段后，原SDK/stream/HTTP真实CSV/PNG备份/恢复/授权导出3项通过（4.80s，second.log），模型始终2次。第一次失败不计为交付通过。
- 迁移/导出/放弃/备份/HTTP综合阶段性98项通过（8.27s，`.drov/t15-final-target-first.log`），之后100项通过（8.44s，second.log）；此后追加回归/CLI/例子，最终冻结结果另记。静态修正Literal计数字段、回调参数名、可选Windows属性和工具结果夹具，未放宽类型检查或依赖锁。

## 最终检查点

最终源码**49ac27481f926f4fc8fa48c68f445a4d30d23415**已推送origin/main，本地验证与下述精确SHA平台CI全部完成；T-15整卡状态及下一项统一见[progress](../planning/progress.md)。最终本机Darwin ARM64，Python3.11.9/3.14.3，uv0.13.0，无新依赖/锁修改。

- 本次最终完整收集 **1394项**。3.14含真实Docker全库 **1335通过/59skip（352.29s）**，`.drov/t15-final-full314.log`；59项为显式空E2B配置后的未授权真实云测试。3.11显式禁用Docker/E2B的完整全库 **1231通过/163skip（201.70s）**，`t15-final-full311-offline.log`，不把此skip算成Docker通过；3.11真实三入口CSV/PNG/备份/导出专项另有实际通过日志。收尾新增43项Python（21历史/故障/CLI/未来格式，22导出/权限/元数据/文件），原3条Docker扩展不重复计新增。
- 两解释器wheel/sdist/重建、四套core-only安装、十一例POSIX SDK/stream通过（`.drov/t15-final-package{311,314}.log`）。两套独立server安装后三场景真实loopback HTTP/SSE与5个工作台资产通过（`installed{311,314}.log`）；Twine strict通过。新迁移/导出例模型均1次，迁移例明确为新库模拟旧字段，真实旧writer证据另见测试。
- 所有90个SDK/资源载荷逐字等于冻结源码，两套构件完全相同：wheel **d1bb0727b05bc493973b9ea3433e7314d52482e17dfd7388cef3d33a600a215c**，sdist **029cd473cd07697aab51229e04e4d83b3c13071f9a86b56b5a767cf2d49ac8f9**（`.drov/t15-final-artifacts.json`）。验收后仅Markdown补记不宣称已在该冻结sdist中。
- 实际隔离载入a83fa79开发wheel（87载荷逐字对应历史Git，SHA256 **fff9bd6c3f2a6fbf38d58273528c95a79e6de1324b8129b459e26ab57f016362**）。它接受真实T-05 writer合成旧库，实际迁移后拒绝新扩展库，两次数据库字节不变（`t15-final-compat.log`）；不是公开发布版本的兼容承诺。
- Ruff、270文件格式、严格mypy182文件、生成schema/OpenAPI、空白检查通过；原core/Artifact/Sandbox/Upload/retention/reclamation/abandonment/barrier DDL、版本、stage/header和容量17项AST保持（`t15-final-original-definitions.json`）。88份Markdown/769相对链接/16段Python AST、原品牌/拷贝、受保护文件与跟踪变更凭据模式检查通过（`t15-final-doc-check.json`）；静态检查不作为任意敏感信息识别或断电保证。

最终[核心38868489017](https://github.com/drov-ai/drov/actions/runs/38868489017) **11 jobs**、[Docker38868489018](https://github.com/drov-ai/drov/actions/runs/38868489018) **4 jobs**均completed/success，逐一核对headSha为上述49ac274完整SHA、所有job均success。实际元数据与完整日志保存在`.drov/t15-final-{core,docker}-ci.{json,log}`；通过`gh run view <run-id> --json headSha,status,conclusion,jobs`及`--log`核实，不从页面绿色标签或旧检查点推断。

| 最终CI范围 | 实际结果与边界 |
| --- | --- |
| Linux3.11 / macOS3.11核心组 | 各1012通过/160skip；该组为SDK-only依赖，不与独立HTTP/MCP/extra组混称全库 |
| Linux3.12/3.13/3.14 / macOS3.14核心组 | 各1014通过/158skip |
| Windows3.11 / 3.14核心组 | 848通过/324skip、850通过/322skip；包含纯SQLite维护/迁移/导出，POSIX文件层仍适用跳过 |
| 八套支持平台独立HTTP / MCP / extras / 安装 | HTTP在POSIX各76通过、Windows各62通过/14skip；MCP各66通过。OpenAPI、wheel/sdist/重建与core-only、MCP/server/E2B/Responses独立安装均success，Windows真实loopback HTTP/SSE安装检查也通过；E2B/Responses只用离线/本地协议夹具，没有付费调用 |
| Python3.15独立探测 | 1014通过/158skip，job实际success；仍是advisory，不扩大支持矩阵 |
| 核心工作台 / Docker工作台 | 核心23通过/1项实际Docker跳过（27.8s）；Docker3.11实际24通过（30.1s） |
| Linux Docker3.11–3.14 | 每套102通过/3skip，实际隔离、故障/硬终止、三入口及安装完成；3skip为未安装对应extra的模块级跳过，不作对应协议通过证据 |

SDK-only组的未安装extra、未显式配置Docker/E2B、Python版本和Windows/POSIX适用跳过均保留在日志；真实Docker由专项独立验收，真实E2B云本轮未授权而未执行。下一项、用户资源与会话建议只维护在[progress](../planning/progress.md)。

冻结前补充：新CLI/共享来源清理与恢复后导出、未来事件包络和字段投影回归已加。一次把持久未来事件v2当作可显示的当前记录导致182通过/1失败（`.drov/t15-final-target-fourth.log`）；原存储按既定规则拒绝未来记录，测试改为断言拒绝，没有放宽读取器；随后183通过（15.08s，fifth.log）。收尾进一步保护工具参数JSON Schema属性、观察元数据/状态/来源hash/事件头不被redactor改写，最终相关187项通过（15.19s，seventh.log）。当前schema版本的未知事件类型只导出元数据；未知持久记录包络版本的离线维护明确拒绝。

冻结前最终专项：补充保存的协议包络/ContextManifest未来版本拒绝，维护快照拒绝未知记录包络；回填/备份不认证无法解释的协议。加入工具schema属性与脱敏器状态/元数据篡改拒绝后，最后综合 **189通过（15.34s，`.drov/t15-final-target-eighth.log`）**；静态Ruff/270文件格式/mypy182文件/schema/OpenAPI通过。新功能源码在此冻结，最终全库/构件/原生平台结果另记。

## T-15任务卡核对（最终平台验证完成）

| 任务卡条件 | 证据位置与实际边界 |
| --- | --- |
| 离线锁/预览、无默认自动删除、Session/键墓碑 | 首批maintenance记录与test_maintenance；原用户数据未作夹具 |
| 未结束/未知事实、原租约/费用保留、data_abandoned | abandonment记录与本轮恢复屏障cancel/新放弃回归；不核销未知资源 |
| 共享来源/ready字节、暂存与过期未消费上传显式回收 | reclamation记录/原库存pending恢复、三入口实际Docker；无清单旧孤儿拒绝猜删 |
| SQLite backup API+原blob/清单、完整恢复/barrier/硬终止 | backup记录及最终三入口扩展；全存储阻断备份之后完全缺失Run/键，未核实不运行 |
| 实际旧Run/事件/派发记录迁移、未来版本/旧SDK拒绝、中断回滚 | 本记录历史writer/21专项/隔离旧SDK；仅具名v1同义回填，不虚构已发布v0 |
| 默认元数据、正文/逐文件read/view_content、脱敏副本 | 22导出专项/实际CSV与PNG/跨归属撤权与恢复后共享来源；无自动外传 |

配对字节仍限POSIX；Windows验证纯SQLite维护/备份/迁移/导出路径，不声称Windows字节层。恢复副本保持屏障，应用独立核查/必要事实重建前不可执行，首版无通用自动/force解除；非通用应用重建工具的交付声明。迁移不覆盖任意未来格式，观察导出不是一致备份/模型重放，业务脱敏由宿主提供，物理擦除/断电/真实模型与E2B本轮未验证。

冻结SDK在本地3.11实际Docker工作台浏览器 **24项通过（18.2s）**，`.drov/t15-final-browser311.log`；完成后只读清点Drov执行容器0（`t15-final-container-inventory.json`），不删除原用户容器/库/字节。迁移WIP的自建临时stash确认全部路径已在49ac274中保存后清除，没有删除用户stash或私有环境。最终验收补记仅Markdown，不修改已验证SDK/测试/构件载荷，也不把补记宣称为冻结sdist中的内容。

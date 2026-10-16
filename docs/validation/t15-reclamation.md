# T-15 第二批：字节回收与中断台账验证

日期：2026-10-16（Asia/Shanghai）。起点Local `/Users/yanlong/projects/drov`，main/f991f41，干净且与origin/main同步；已阅读AGENTS/progress/导航/handoff、T-15任务卡/契约和首批源码/验证。前置最终代码9fd41701beb31b273bccda981d79e136c2d6b27e的核心11/Docker4 jobs全部success，已保存元数据绑定SHA及完整日志见首批记录。本轮不重新规划，不进入T-16。

## 本批范围

按[ADR-0028](../decisions/0028-resumable-byte-reclamation.md)实施原SQLiteMaintenance的显式preview_reclamation/reclaim/resume_reclamation、独立reclamation_schema=1与pending/done库存、POSIX原目录FD删除/同步和普通打开阻断。有效阶段按owner/Agent整批回收；共享引用/未知执行/租约/已消费上传继续保护，过期未消费上传需显式选择。API/运行/工程/架构/维护/案例/质量/生成参考同轮同步。

T-15整卡仍进行中；data_abandoned、一致备份/恢复/recovery barrier、实际迁移与授权导出未完成。无有效原清单/可信归属且没有pending库存的旧孤儿目录保持拒绝，不猜测owner强删。返回数据长度/容量不等于物理安全擦除、SQLite空闲页回收、备份副本删除或云预算退款。只对临时合成库/文件运行删除，原用户库/字节/租约/私有env/云台账与品牌保留；未读取真实密钥、付费模型/云调用、公开发布或部署。

## 实际失败与修正

- 首轮31项为27失败/4通过（2.57s，`.drov/t15-reclamation-first.log`）：候选库存包含datetime，严格JSON摘要正确拒绝；改为实际JSON序列化，不放宽JSON规则。独立进程不能导入tests包，夹具改为只导入SDK并自建同scope；上传消费夹具依次修正私有_upload_ids参数和实际Runtime.wait，未改生产接口迁就测试。
- 第二轮29通过/2失败（2.17s，`.drov/t15-reclamation-second.log`）：除上述wait夹具外，旧终态夹具保留操作行但未保留summary；回收以持久operation事实继续保护unknown/dispatched，若已有summary与之冲突仍拒绝，不由终态或缺summary推断可删。第三轮31通过（2.22s，third.log）。
- 增加真实取消线程、容量准入、替换目录/链接/权限、未来版本与真实CLI后，41项通过（3.40s，`.drov/t15-reclamation-fourth.log`）。类型检查发现独立夹具的替换方法参数名与原方法不符及Literal忽略标记位置，按真实签名/类型修正；没有放宽严格检查。
- 三入口真实Docker扩展初跑3通过（4.46s，`.drov/t15-reclamation-docker-first.log`）：原输入回收时仍共享的CSV/PNG可授权/hash读取，最后引用清理后实际回收输出，原模型计数仍2。

## 最终检查点

代码/测试检查点 **ffec4d12ed787dcf1e6d3b8a686677eefd25473e** 已推送origin/main；后续补记只改Markdown，不将其伪称已包含在本次sdist。

- 最后补充管理员绕过普通打开后新增内容引用的回归：恢复再次扫描同scope内容与Run/未知操作/引用，42项通过（3.43s，`.drov/t15-reclamation-fifth.log`）。最后两解释器维护/字节/SQLite进程/Runtime故障专项各 **161通过（11.86/13.20s）**，`.drov/t15-reclamation-final-target{311,314}.log`；此前各117项（6.86/7.30s）是增加该回归和恢复检查之前的检查点。
- 两解释器wheel/sdist重建、四套core-only干净安装、七例normal/stream均成功（`.drov/t15-reclamation-package-final{311,314}.log`）。POSIX维护例子真实接收/删除合成CSV、回收8字节、重复恢复零，旧请求Gone且模型调用仍1。两套独立server安装后的三个参考场景真实loopback HTTP/SSE和5个工作台资产通过（`t15-reclamation-installed{311,314}.log`）。
- 所有84个SDK/资源文件逐字等于本次源码，两个解释器构件摘要相同（`.drov/t15-reclamation-artifacts.json`）：wheel **9b3c1ac986e275e922ad81b7974c1eba02b2a21db32cf54596887e87a2073bfa**，sdist **cd92df558e439aaf03da3862b88aaf04f872101d199be88647f67cf2724b74e2**；Twine strict通过（`t15-reclamation-twine.log`）。
- 原core DDL/user_version/record_schema、Artifact/Sandbox/Upload/retention DDL与记录、stage/header及容量常量做实际AST比较均保持；uv.lock/P4/品牌原素材无变化。Ruff、245文件格式、mypy165文件、生成schema/OpenAPI及Git空白检查通过；80份Markdown/690相对链接/16段Python片段AST初次复核无错误，收尾再核对。
- 实际从历史9fd4170开发wheel隔离载入旧SDK；其83文件逐字等于9fd4170，SHA256 **8acec190764151f32df32907417ffbe364e5e5fddc29986aecf1c634ab8599f8**。旧SDK接受原Artifact库，拒绝本批reclamation扩展库，两库字节均不变（`.drov/t15-reclamation-old-compat.log`）。不是公开发布版本或实际旧记录迁移证明。

工具环境修正也保留：最初两个package入口在PATH无uv时exit2（`t15-reclamation-package{311,314}.log`）；显式用户目录uv实际为0.11.30，被仓库==0.13.0门槛拒绝（`package-accepted{311,314}.log`）。随后使用原`.drov/tooling/bin/uv`实际0.13.0，才有上述final构件成功，没有改全局uv或锁。旧SDK夹具首次因macOS `/var`临时路径含符号链接被原保护拒绝，改用解析后的合成临时目录；日志`old-compat-first.log`。Twine首次误用不含该模块的tooling解释器，改用现有3.11环境后通过，原失败见`twine-first.log`。这些准备失败不算安装或兼容成功。

本机Darwin ARM64，Python3.11.9/3.14.3、uv0.13.0；本次最终Python3.14含真实Docker全库 **1226通过/59skip（326.64s，1285项）**，`.drov/t15-reclamation-full314.log`。显式置空DROV_E2B_TEST_CONFIG，59项为未授权的E2B真实远端测试；不是云验证成功。3.11本地本批证据为上述161专项/构件/安装，未冒充最新全库重跑。合成费用探针实际在回收7字节后保留相同累计费用金额，没有云调用（`.drov/t15-reclamation-cost.log`）；该探针首次系统Python无drov而未执行，改现有venv后才通过。

最终 [Docker38853895681](https://github.com/drov-ai/drov/actions/runs/38853895681) 全部4个job completed/success；实际元数据headSha为ffec4d12ed787dcf1e6d3b8a686677eefd25473e，完整证据`.drov/t15-reclamation-docker-ci.{json,log}`。Linux原生Docker3.11–3.14每套102通过/3适用skip，3.11另跑实际Docker浏览器24项全通过；包括原三入口CSV/PNG的本批回收扩展。回收42项离线库存/故障测试在核心POSIX矩阵执行，不能将Docker的deselected算成它们通过。本次本地Drov执行容器只读清点0（`.drov/t15-reclamation-container-inventory.json`），不清理原用户容器/数据/云资源。

最终[核心CI38853895835](https://github.com/drov-ai/drov/actions/runs/38853895835)全部11个job completed/success；实际headSha同为 **ffec4d12ed787dcf1e6d3b8a686677eefd25473e**，元数据/完整日志为`.drov/t15-reclamation-core-ci.{json,log}`。Linux/macOS3.11核心各905通过/160skip；Linux3.12–3.14及macOS3.14各907通过/158skip；Windows3.11为752通过/313skip，Windows3.14为754通过/311skip，均正常退出并完成后续构件/安装/extras步骤。新增回收42项按POSIX限定在Windows跳过，不据此声称Windows字节支持；Python3.15 advisory核心907通过/158skip，保持advisory定位。核心工作台23通过/1实际Docker适用skip，实际Docker浏览器结果见上文；各平台server/MCP/离线Responses/E2B及独立安装步骤均success，不将核心skip当作这些专项通过。

**第二批有效阶段回收子范围已验收；T-15整卡仍进行中。** 未交付data_abandoned、一致备份/恢复/barrier、实际迁移和授权导出，不能据本批pending恢复推断旧备份安全。收尾仅Markdown；80份Markdown/690相对链接/16段Python AST、受保护文件、凭据模式和空白复核见`.drov/t15-reclamation-doc-check.json`，不将静态检查写成运行或安全保证。具体下一项、恢复步骤与用户资源只维护在[progress](../planning/progress.md)。

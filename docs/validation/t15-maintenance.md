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

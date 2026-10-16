# T-15 第四批：一致备份与恢复屏障验证

日期：2026-10-16（Asia/Shanghai）。同一Local `/Users/yanlong/projects/drov`，承接第三批a4c8c7a95eea1dfe846f3f297b97aec3b9435005；其本地1254通过/59skip、构件/安装已完成，Docker CI38858719894四项success、核心CI38858719899当时仍在Windows安装检查，其余9项success，最终平台状态见[第三批记录](t15-abandonment.md)。同一T-15内接续独立实现，不进入T-16。

## 范围与限制

按[ADR-0030](../decisions/0030-quarantined-consistent-backups.md)实现真实SQLite backup/原文件成对复制、严格清单/hash、inspect_backup、不可覆盖新目录、发布前副本持久屏障和restore；旧副本直接打开也不能接收任务或从控制/模型/工具/沙箱恢复派发。查看/维护可用，不自动解除屏障或重建备份之后的外部事实。原记录/扩展DDL与版本不改，只有新副本启用recovery_schema=1；不是实际记录迁移、授权脱敏导出、进程/容器/云快照、物理擦除或断电耐久性认证。T-15整卡仍未关闭。

只对临时合成数据及既有本地Docker运行；原用户库/字节/租约/云费用、私有环境与品牌保留，没有读取真实Key、调用付费模型/云、公开发布或部署。

## 实际检查与修正

- 首轮9项为8通过/1失败（0.61s，`.drov/t15-backup-first.log`）：测试误用了不存在的Runtime.get，改用实际inspect().snapshot；下一条误写不存在的专项文件名导致未运行（`second.log`），随后原存储/放弃/HTTP与新增备份71项通过（3.03s，`third.log`）。这些准备错误不算通过。
- 增加未知/审批/租约/取消等测试后，先前Ruff移除的未用导入尚未补回，第四轮11通过/7个NameError失败（1.19s，`fourth.log`）；补实际导入后18项通过（0.89s，`fifth.log`）。随后加入6个实际子进程kill及ready损坏/配对/链接等，30项通过（2.26s，`sixth.log`）。没有放宽保护或静态检查。
- 增加verifier/CLI/HTTP后，HTTP夹具的session参数误写session_id，第七/八轮各35通过/1失败（3.83/3.98s，`seventh/eighth.log`）；按实际夹具参数修正，后续最终结果另记。静态类型修正两个线程回调的绑定类型，未使用放宽mypy或新增依赖。
- 原三入口真实Docker SDK/stream/HTTP闭环扩展：共享来源正文清理/输入回收后备份，恢复CSV/PNG，经HTTP授权下载/hash逐字相等，barrier仍在且model调用维持2；三项通过（4.70s，`.drov/t15-backup-docker-first.log`）。本次没有重新调用真实模型或E2B。

## 最终检查点

最终本地源码检查通过，待本次提交推送后记录精确SHA/平台CI；当前不提前关闭本子范围或T-15。

- 第九轮36项通过（3.95s），SDK/stream新例通过，模型均1次；追加观察旧accepted/running不改游标和pending回收前拒绝备份后，最终备份/HTTP39项通过（4.24s），`.drov/t15-backup-ninth/tenth.log`。本批新增36项Python（35备份/故障、1HTTP），原3Docker场景为扩展，不重复算新增。
- 本机Darwin ARM64、Python3.11.9/3.14.3、uv0.13.0。最终3.14含真实Docker全库 **1290通过/59skip（331.81s，1349项）**，`.drov/t15-backup-full314.log`；E2B配置显式为空，59项未授权真实云测试适用跳过。3.11维护/放弃/回收/备份/HTTP/实际进程/SQLite/Runtime故障专项 **187通过（19.11s）**，`t15-backup-target311.log`，没有冒充3.11最终本地全库。
- 两解释器wheel/sdist/重建、四套core-only安装、九个POSIX例normal/stream通过（`.drov/t15-backup-package{311,314}.log`）；两套独立server安装后三个参考场景真实loopback HTTP/SSE和五个工作台资产通过（`installed{311,314}.log`）。新例actual SQLite backup/inspect/restore/CSVhash读取与缺失键屏障，model=1，无外部模型。
- 两套构件相同，全部87个SDK/资源文件逐字等于冻结源码，wheel **b75643a196b99cccb3abebab3a696f517952c00038469c4e7a94e56d07687993**、sdist **a8e15456a254e2cc308c76bebb6610c86aa2393e45c10fe21a169bf00f384b47**；Twine strict通过。旧a4c8c7a开发wheel85载荷逐字对应历史Git，SHA256 **5e51bb6861f06ae1bccc497c60c056a582e9d045ae395d9bd90e1ad4f9ae4264**，实际隔离读原库成功、拒绝recovery扩展副本且两个库字节均未变（`t15-backup-compat.log/artifacts.json`）。不是公开旧版本迁移证明。
- Ruff、258文件格式、严格mypy174文件、生成schema/OpenAPI与空白检查通过。原core/Artifact/Sandbox/Upload/retention/reclamation/abandonment DDL、版本、stage/header/容量16项AST保持（`t15-backup-original-definitions.json`）；uv.lock/P4/品牌未改，84份Markdown/735相对链接/16段Python AST与跟踪变更凭据模式通过（`t15-backup-doc-check.json`）。静态检查不等于任意安全或物理断电保证。

最终源码/平台CI完成后另补，不把后续迁移/导出工作套用本批结果；构件包含冻结时的文档，后续验收Markdown补记不宣称已在旧sdist内。下一项、用户资源及会话建议只维护在[progress](../planning/progress.md)。

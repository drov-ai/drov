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

最终专项、全库、构件/安装与平台结果在源码冻结后补记；以上阶段结果不替代最终检查点，也不作为关闭整卡依据。具体下一项、恢复步骤与用户资源只维护在[progress](../planning/progress.md)。

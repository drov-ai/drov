# ADR-0030：成对一致备份、原子发布与整个存储的恢复屏障

日期：2026-10-16（Asia/Shanghai）。T-15第四批，承接ADR-0027/0028/0029；状态/实际验证见[progress](../planning/progress.md)/[本批记录](../validation/t15-backup.md)。原core/Artifact/Sandbox/Upload/retention/reclamation/abandonment DDL与版本、P4历史指纹不改。

## 原因

SQLite事务、字节回收台账和data_abandoned都不能证明旧备份之后发生了什么。只对备份里已有Run设置暂停，会漏掉备份之后新建且已经消失的Run/去重键；只复制数据库，也可能产生ready但没有字节的引用。维护需要真正可校验的副本与默认阻断整个存储的边界。

## 决定

- 原SQLiteMaintenance取得Runtime同一数据库/原配对字节所有权，停止运行后显式backup。SQLite使用实际connection.backup API，原文件目录通过原目录FD读取，保留store.json、全部有效stage.json和现存普通字节，包括预留/未完成阶段；不把未完成阶段升级成ready。不复制进程、容器、VM内存或云资源。原库/文件不因备份升级或清理，未知操作/租约/累计费用、墓碑/保留来源/放弃事实跟随副本。
- 首先核对SQLite完整性/原DDL/记录索引与版本、原配对、ready引用和真实文件hash；pending字节回收必须先resume，未知执行或活动租约可被保留备份。新副本再次验证上述关系，ready缺失/损坏拒绝发布，不静默降级或重新执行。inspect_backup验证完整库存、schema、配对和ready字节，返回管理员manifest。
- 目标必须为已有父目录下的新目录，已有目录即使为空也拒绝覆盖。根目录持有独立本地排他锁；在partial随机子目录写入/同步全部载荷、manifest，再原子改名为data。只有data/manifest.json构成完整备份；中断遗留partial不作为恢复输入，不自动删除其中证据。restore同样向全新目标写入，不能就地替换原数据库或任意拼接两个路径。返回目标data/state.sqlite及可选data/artifacts的管理员路径。
- manifest格式v1包含快照ID/时间、原记录/扩展版本、配对ID/限制，以及数据库、header、每个阶段/实际字节的大小与SHA-256；只接受固定内部相对路径、严格库存和私有普通文件/目录，无解包逻辑。校验和用于一致性检查，不是对拥有本地写权限的管理员进行签名认证。完整备份是原始敏感数据副本，不是脱敏用户导出；宿主负责访问权限、磁盘保护和外部副本生命周期。
- **在备份副本发布前就写入recovery_schema=1独立扩展和唯一recovery_barrier事实**，原库不写。直接把完整备份里的数据库当Runtime使用，也受到同一屏障。restore先验证源包，再复制原成对载荷，在发布前写新restore事实，绑定源manifest摘要并保留前一屏障摘要。旧SDK拒绝新扩展；未来版本、缺失/损坏事实或缺flag但有表都拒绝。源SQL/字节格式保持，不把本批称为原记录迁移。
- 屏障阻止存储层接收（包括旧同键）、Runtime.start/run/stream及respond/resume/recover/verify，以及内核模型/Python工具/沙箱派发与恢复。它覆盖原库完全没有的新Run/键。查看/授权文件读取和离线维护仍可用；启动扫描/交互过期不自动改写被屏障保护的游标。配置资源的宿主初始化仍遵循原生命周期，不把屏障宣称为任意Python/操作系统的安全沙箱。
- 不提供通用自动解除、force或仅凭“拿到锁/数据库没有记录”解除的入口。恢复副本在独立业务/供应商证据核对及必要事实补齐之前保持阻断；普通审批、verify、data_abandoned、正文删除/回收都不能解除。框架不能以一段任意文字替代外部核实，也不从本批推断已提供应用专用的事实重建/解锁工具。
- 取消必须等待实际SQLite/复制/发布工作结束后再释放所有权；可能完整发布后向取消调用者抛CancelledError，目标不会因此回滚覆盖原数据。完成回执不明时先inspect_backup或查询新目标屏障，再选择新的目标，不覆盖重试。普通错误/硬终止可能遗留私有partial，保持拒绝恢复，可由管理员核实后另行处理。

## 替代方案与代价

不采用独立复制两个目标、裸文件拷贝SQLite、仅保护已有Run、恢复时自动执行prepared或提供无证据force解锁。选择不可覆盖的新bundle与副本内持久屏障，代价是额外磁盘空间、全库/文件校验、旧SDK不能打开新副本，以及应用专用核实之前不能继续运行。POSIX同步文件和各层目录；Windows纯数据库路径使用文件flush/SQLite FULL及目录原子改名，未声称验证了Windows目录fsync或物理断电；配对字节仍只支持POSIX。

## 验证

合成库/真实SQLite backup、空库/备份后缺失Run和键、原审批已执行后旧副本、未知写入/verifier零调用、租约/费用/墓碑/跨Session共享来源、实际CSV/PNG销毁后授权/hash读取、版本/库存/配对/字节损坏、拒绝覆盖/原锁、取消实际worker和复制/发布硬终止边界。脚本模型不是实际模型质量，合成E2B租约不是云资源验证。通过/失败/未跑仅见[记录](../validation/t15-backup.md)。

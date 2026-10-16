# ADR-0028：显式字节回收与可恢复删除台账

日期：2026-10-16（Asia/Shanghai）。T-15 第二批工作决定；实际验收和剩余任务见 [progress](../planning/progress.md)。继承 ADR-0027 的 Session 逻辑删除，不改变普通请求/授权或历史 P4 指纹。

## 原因

SQLite 删除和文件系统删除没有跨介质事务。先删文件可能产生悬空 ready，先删阶段清单又可能在中断后丢失归属与容量事实。需要单独的离线动作，先固定引用判定和删除意图，中断后仍能核对剩余目录。

## 决定

- 在原 SQLiteMaintenance/排他锁内提供 `preview_reclamation(MemoryScope, expired_uploads=False)`、`reclaim(plan)` 和 `resume_reclamation()`。按 tenant/user/Agent 精确选择；默认没有 TTL 或后台删除。预览只返回数量、容量、保护原因和完整快照 HMAC，不返回正文、逻辑名、路径或阶段 ID；reclaim 重算并完整比较原计划。
- 整个 stage 是物理回收单元：任一 ready/unavailable 元数据、合法共享引用、保留的输入/同 scope 内容引用、上传或执行租约仍占用该批次，就保留整批。未结束 Run、终态仍未知/已派发操作、活跃/未确认销毁租约保护原阶段；不能仅由终态推断可回收。无原 Run/墓碑依据的 output 阶段保守保留。其他 owner/Agent 的阶段不在本次范围。
- 只有显式 `expired_uploads=True` 才选择预览时已过期、未消费的 receiving/staged 上传；已消费、尚未过期及仍有其他保护的上传保持。上传元数据移除与回收意图同一事务，不能在间隙再消费原暂存。不改变原上传 TTL 和消费协议。
- 首次实际回收显式启用独立 `reclamation_schema=1` / `artifact_reclamation`，原 core/Artifact/Sandbox/Upload/retention DDL 与版本不变。旧 SDK 因未知扩展表拒绝；新版仍能打开原格式，未知版本/坏台账拒绝。每阶段保留 store/stage/opaque Run ID、scope/清单 HMAC、目录和文件 inode/设备/长度/修改时间、容量及 pending/done 时间；不保留逻辑名、内容或明文 owner。
- 先提交所有 pending 意图，再通过私有目录 FD 检查整个剩余清单，拒绝链接、硬链接、替换目录、额外项或修改文件。只删除原清单中的实际字节，再同步目录、移除 stage.json、同步、移除阶段目录并同步根目录，最后逐阶段提交 done。SQL 意图回执未知时不开始删；字节删除/同步或完成提交失败时保留 pending；进程取消必须等实际字节 worker 结算后才放锁。
- 有 pending 时普通 SQLiteStorage.open/Runtime 拒绝打开，即使没有装配字节资源也不放行。离线入口只允许恢复原台账，其他预览/逻辑清理/新回收拒绝。它只跳过台账明确列出的待删阶段，不能忽略其他缺清单或未知目录。恢复重新核对数据库引用/Run/未知操作/上传/租约及剩余文件身份；缺少已列文件/目录可视为该意图先前进展，重新同步后完成，不重写输入、不调用模型或后端。
- bytes_reclaimed 是本次确认完成阶段的原清单内实际数据文件长度之和，可能包含上次中断前已删的字节；capacity_released 另含原预留和固定清单额度。它们不计磁盘块、SQLite 空闲页、外部备份或安全擦除。完成后重复 resume 返回零；累计审计以每阶段 done 台账为准，不能把每次恢复的结果当独立费用退款。pending 完成前普通准入被整体阻断，不能提前利用部分空闲容量。云累计费用预留不因本地字节回收变化。

## 替代方案与代价

将 SQL 与字节删除合成“原子删除”无法覆盖进程退出；自动 TTL 会破坏恢复输入；逐文件回收又需改写现有 stage 清单和部分预留算法。当前选择整阶段保守回收和独立 SQL 台账，代价是共享一文件会保留同批其他文件，预览扫描全库/清单，pending 会阻断整个库的普通打开。持有宿主文件能力的管理员仍可破坏数据，本实现不承诺对恶意宿主或物理断电的保证。

不为无有效清单/无可信归属的旧孤儿目录推测 owner，也不将本批恢复当作旧备份恢复屏障。data_abandoned、一致备份/恢复/barrier、实际记录迁移和授权导出仍按 T-15 接续，未验收前不关闭整卡。

## 验证

真实 SQLite 和私有字节夹具覆盖精确 scope、整批共享保护、过期/消费上传、容量准入、修改/替换/链接拒绝、I/O/提交失败、取消实际线程结算及各提交/逐文件/目录硬终止；原真实 Docker SDK/stream/HTTP CSV/PNG 路径扩展回收前后授权/hash/Gone。实际结果单独记录于 [第二批验证](../validation/t15-reclamation.md)，不从说明推断全部通过。

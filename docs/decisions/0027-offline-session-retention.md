# ADR-0027：离线会话逻辑清理与保留事实

日期：2026-10-16（Asia/Shanghai）。工作决定；实际验收和剩余范围只见 [progress](../planning/progress.md) 与 [验证](../validation/t15-maintenance.md)。对应 T-15 第一批，不关闭整卡，不改变首版范围或历史 P4 指纹。

## 原因与边界

原 SQLite、Artifact、上传和沙箱表已有真实记录，但尚无离线清理入口。直接删除 Session 会丢去重事实，Artifact 读取还依赖原 Run；删除 E2B 的完成租约也会错误减少累计费用预留。需要将正文与仍须保留的事实分开，并保护未结束/未知执行。备份/恢复、跨介质回收和迁移具有不同故障边界，按原任务卡分批实现，不以数据库删除替代其验收。

## 决定

- `drov.storage.maintenance.SQLiteMaintenance` 是可信本地管理员持有的文件系统能力，复用 SQLiteStorage 的同一排他锁；配对库还必须提供原 LocalArtifacts 并取得其锁。只打开现有库/配对目录，不隐式新建 SDK 数据或迁移。构造无 I/O，普通 Runtime/HTTP/工作台不暴露管理入口；持有该宿主文件能力的调用者可管理指定归属，不从普通 HTTP 身份推导管理员权限。
- `preview_session(SessionScope, include_memory=False)` 返回数量、阻止原因、引用保留数量、未引用阶段数量及带密钥快照摘要，不返回输入、事件正文、文件名、代码或工具参数。摘要涵盖整个库和阶段清单，防止另一会话的授予/操作变化使旧预览失效。数据不变时可重开后使用同一摘要；任何改变都需重新预览。没有后台清理或新增用户审批流程。
- `delete_session(plan)` 在一个 SQLite 事务内重算并逐字段比较原预览。未结束 Run、活动 Session、dispatched/unknown 操作、活跃或未确认销毁的租约一律拒绝；failed/cancelled 本身不证明效果已核清。显式同删 Memory 针对该用户/Agent 的整个 scope；其他相关会话未结束或仍有未知操作时同样拒绝。默认保留 Memory。
- 原 core records DDL、user_version=1、record_schema=1 及 Artifact/Sandbox/Upload 版本不变。首次实际清理同事务启用 `retention_schema=1` 和独立 `retention_tombstones`；预览不启用。新版仍读原库，旧 SDK 必须因未知扩展表拒绝；未知版本/坏 DDL 拒绝。摘要使用现有存储的 dedup_secret，不保存原 scope/request_key 或内容指纹；允许保留 opaque Session/Run/Artifact ID、终态和清理时间。去重墓碑不自动到期。
- 清理的 Session 不可隐式重建；同 scope 的旧 request_key、换 request_key 或无 request_key 也返回 `ContentGone`，必须换新 Session ID。原 request_key 的摘要另留墓碑。原 Run 的读取/控制和已清理 Artifact 返回 410，跨 tenant/user 猜测仍 404；Run/Artifact 的 Gone 投影仍复核当前 read，撤权不泄露存在性。既有同 tenant 跨用户读取的授权路径保持不变；已清理事实的可见性只保留给原 owner 的当前 read，其他身份统一 404，不另存可遍历的归属目录。
- 清理原 Session 前移除其 grants；另一 Session、保留的输入/模型/工具记录、上传或执行租约仍有引用时，保留 Artifact 元数据和字节。原 Run 被删除后，以带密钥、绑定 owner/Agent/原 Run/operation/ref/逻辑名/store/stage 的来源证明继续校验共享文件。证明不保存文件名正文，知道 ID 或出现文本引用仍不授予权限。文本提及只会保守保留，可能延迟释放。
- 最后引用清理时删除 Artifact 元数据、留下最小墓碑；已销毁租约和该 Session 的上传元数据随同清理。E2B 已预留的完整寿命费用在同一事务转移到按预算 ID 带密钥摘要索引的累计金额，再删除租约。新准入同时计入原租约和保留金额，不退款、不重算、不要求访问云服务。

这里的文件系统权限是管理员入口，不是加密或操作系统隔离保证。`bytes_reclaimed` 在这一批恒为 0：所有 stage.json、原始字节及其容量计费保留，包括已逻辑清理文件和暂存上传。这一批不宣称完整个人数据删除、物理擦除、释放容量、垃圾回收台账或一致备份已完成。

## 替代方案与代价

继续保留完整旧 Run 可省来源证明，但会保留用户正文；只保留 request_key 则无法阻止无键重建旧 Session。重写 core DDL 会扩大历史兼容面，因此采用显式扩展。仅依赖 grants 会漏掉输入快照/工具与执行证据，当前保守扫描保留记录；代价是离线预览扫描全库，暂不承诺大库性能。字节回收暂不与 SQL 删除捆绑，以免跨介质中断后生成悬空 ready 引用；后续须独立交付显式回收及中断台账。

该决定没有提供 data_abandoned 管理动作、backup/restore、recovery barrier、实际旧记录迁移或正文/文件导出 API，也不创建其空壳。它们仍按 T-15 原验收条件接续，未完成前不进入 T-16 或关闭 T-15。

## 验证

真实 SQLite 事务失败/提交丢应答/提交取消及进程硬终止、SDK/HTTP 当前授权与 410/404、跨 scope、显式 Memory、共享引用和实际 Docker CSV/PNG 见 [验证记录](../validation/t15-maintenance.md)。模型是固定合成夹具；E2B 费用检查只读写本地合成台账，不调用云或真实模型。

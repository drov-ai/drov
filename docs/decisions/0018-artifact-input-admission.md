# ADR-0018：文件接收、Run 授予与授权读取

日期：2026-10-14（Asia/Shanghai）。状态：T-10A 第二批已实施，整卡仍未完成；验证见 [输入与授权记录](../validation/t10a-artifact-inputs.md)。沿 ADR-0016/0017 接入文件通道，没有实现任意代码执行或输出产物发布。

## 原因与选择

只有私有暂存无法给 Run 授权或返回 SDK 下载。既有 records.kind 有固定 CHECK，不能把 Artifact 伪装成 context/receipt，或为文件重写已验收主表。选择显式 ArtifactStore 配置启用 SQLite 扩展 v1：新增 artifact_records、artifact_grants 与索引，metadata 记录 artifact_schema=1；主表/user_version/Run record 保持 1。构造和普通无文件 Storage.open 不新增扩展；LocalArtifacts.open 的配对事务显式初始化扩展，已存在扩展严格检查完整 DDL。旧数据库/旧记录可读，新版支持原上下文 v1；更旧 SDK 会拒绝扩展表，不能宣称旧 SDK 可写新版文件库。

## 实际决定

1. Runtime 接受 LocalArtifacts 或 owned/borrowed/factory ResourceBinding。打开 SQLite 后配对/open 字节层，排他租约覆盖整个 Runtime，逆序关闭自有资源；借用必须已打开且与同一个 SQLite 实例配对，不关闭宿主借用资源。内存存储、缺 store、临时 Agent 文件输入在模型调用前拒绝。
2. start/run/stream 接收有序 ArtifactInput/ArtifactRef。规范 request_key 摘要含输入 NFC 名/hash/大小/类型或完整原 ref；空清单不新增摘要键。先 lookup 原 Run，后检查新接收容量和写字节。新输入先私有预留/可靠暂存/校验，再在同一 SQLite 接收事务保存 ready 元数据、原 Run/文件绑定、Run 授予、去重、检查点及接收事件；失败全部回滚，已落盘私有阶段保留容量、不可下载。接收调用方取消不撤销已归控制 Task 的事务，重试复用同一 Run。输入没有代码资源，无需销毁事实；**输出产物仍必须按 ADR-0016 的销毁前暂存/销毁后结果共同发布**。
3. 原始归属/Agent/来源和目标 Run 授予分别保存。ArtifactResource 使用既有 read/view_content，含原 Agent/run/operation、artifact_id 与目标 run/Agent/session；tenant 边界不可覆盖，默认本人且不跨 Agent 复用，显式 Authorizer 可允许同 tenant 的共享引用。引用 ID/name/hash 与 ready 字节逐一核对，授权在 I/O 后再复核；不能用正文中提到旧 ID 自动授予。原字节不复制、不转移原归属。
4. get_artifact 返回受授权元数据；include_content 才给逻辑名。read_artifact 接受 ID 或完整 ArtifactRef，后者另外复核调用方固定 hash/大小/类型；两个入口都读前/后复核，字节有界校验，缺失/损坏拒绝并提交 unavailable，不重算。未发布私有阶段无索引、不可查询或读。默认 Run/上下文投影不泄露名称/字节/主机路径。
5. 获准描述进入受预算保护的当前输入/来源：仅 ID、名、hash/大小/类型和虚拟 sandbox_path，不含字节或宿主路径；Artifact grant 不把文件内容当指令，也不代表现在存在可执行代码环境。正文 Run 查询及模型调用前重新检查当前文件授权；模型调用前检查字节。已接收输入在首次模型派发前丢失是 durable failed/artifact_unavailable，模型调用 0，不能冒充 SQLite 不可用或成功输出。
6. 新增来源枚举 artifact，因此含文件的 ContextManifest 明确用 **schema_version=2**，无文件继续用 v1；新版读取 v1/v2，v1 不允许 artifact，boolean/未知版本拒绝。Artifact 扩展/Ref/Info/Grant 各自 v1；记录与 public schema 的增量同轮生成。Runtime 保存字节 store ID、扩展版本及限额 hash，继续原 Run 时复核，不在新配置下猜测旧批准/输入。

## 替代方案、代价与影响

重用旧 RunItem kind 会混淆已有解码/索引责任；升级整张主表到 v2 会提前引入一般迁移；独立文件数据库会失去输入授权与 Run 接收事务。所选扩展保留旧主表，但新增表后旧 SDK 明确不兼容，T-15 需要包含扩展/字节/配对与活动台账的备份和维护。每次授权/有界 hash 校验有 I/O 成本；撤权不能撤回已经发出的文件或模型输入。私有未消费阶段仍无自动删除/恢复入口。

API/运行/工程/架构/维护/参考示例、schema 与 progress 同轮同步。API ready 输入/下载只验收本批文件通道；审批、PythonSandbox/DockerSandbox、安全输入副本、导出器、输出 ready/工具结果事务和沙箱租约/恢复未实施。T-10B/T-10C 不提前开始；历史 P4、uv.lock、品牌和首批镜像锁/许可不变。

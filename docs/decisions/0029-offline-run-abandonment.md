# ADR-0029：显式放弃遗留运行与最小未知事实

日期：2026-10-16（Asia/Shanghai）。T-15 第三批工作决定；承接 ADR-0027/0028，不改通用框架范围、原记录DDL或历史P4指纹。任务状态与验证分别见[progress](../planning/progress.md)/[本批记录](../validation/t15-abandonment.md)。

## 原因

原 Runtime 遇到无法核实的副作用会保留核查，普通取消/失败不能将其当作未执行。原维护也正确拒绝删除这些事实，但需要已在运行契约约定的离线管理员放弃入口，允许明确停止推进并保留未知说明。不能借此删除未确认销毁的沙箱台账，或重试原代码。

## 决定

- 在同一 SQLiteMaintenance、原 SQLite/字节排他锁下提供 preview_abandonment、abandon_run、get_abandonment；无HTTP管理入口。原拥有者尚持锁时拒绝，未结束实际调用仍遵循Runtime的锁保留；锁释放不证明外部操作/容器已停止，管理动作也不声称这项证明。仅 accepted/running/suspended 可首次放弃，普通终态不能转换成放弃来绕过保护。
- 管理员必须给出1–512字符、非空白、单行可打印的原因。预览返回目标scope/Run、原状态/版本、未知操作和保留租约数量以及全库/阶段HMAC，不返回用户输入、参数、代码、正文或文件名。完整计划变化/篡改拒绝；原因是管理员提供并将长期保留的管理说明，应使用不含正文或凭据的文字。
- 在一个原SQLite事务内：prepared操作按原终结规则记not_executed，原dispatched/unknown及已知结果原样保留；尚pending交互记abandoned，历史已消费回执不改；Run转为failed，error.code=data_abandoned、retryable=False、recoverable=False，清除当前pending/reason，释放Session活动Run指针。已知用量保留，complete=False。原检查点进度不再可恢复，普通recover/respond/同键start不得重派发；同键仍指原失败Run。
- 同事务启用独立abandonment_schema=1/abandonment_facts。事实只保存opaque Run/Session、Agent、终态/不可恢复标记、原状态/最终版本、管理原因、未知operation/tool/revision/effect/status、保留租约数量和时间；owner/request_key不明文保存在事实目录，按原存储密钥HMAC查询。原core/Artifact/Sandbox/Upload/retention/reclamation DDL与版本不变；旧SDK必须拒绝新扩展，未知版本/坏事实拒绝。普通SDK/HTTP终态消息不回显管理原因。
- 原完整预览的摘要另绑定不可变回执。实际提交后丢回执/取消，重开原存储并重试同一计划只返回同一事实，不新增事件/版本；换原因或计划冲突。内容已按规则清理后仍可查原事实。发生不确定事务时遵循原存储poison保护，不在原实例强行重试。
- 普通Session清理只有在failed/data_abandoned、版本/Agent/Session和当前未知操作逐项匹配该事实时，才允许去除相应未知操作正文；没有事实或事实不符仍拒绝。独立未知事实不随Session正文删除，旧请求和Session ID的墓碑规则不变。活动/未确认销毁租约仍阻止清理，所有租约/费用预留在放弃动作中原样保留，不调用后端、不退款。租约数量为放弃当时的历史快照，不宣称这些资源当前已销毁。
- 字节回收仍须另行预览/执行；放弃不会自行释放字节，保留的Run/未知操作/输入/租约仍保护原阶段。只有后续符合条件的逻辑清理及最后引用移除后才可回收。pending字节回收期间不允许新放弃；最小事实的管理员只读查询不派发动作。

## 替代方案与代价

把unknown改成failed_known、复用cancel或擦掉操作会制造“外部未执行”的错误结论；只保留完整Run则不能按既定策略清理正文。选择独立最小事实和不可恢复终态，代价是新增显式存储扩展、事实长期保留，以及有未清理租约时仍不能删除正文。它不是外部业务核实、资源销毁、备份恢复或解除recovery barrier的操作。

## 验证

accepted/running/suspended、完整计划冲突、原锁、已派发/未知保留与prepared未执行、SDK/stream/HTTP暂停后不可批准/恢复、最小事实/墓碑跨重开与正文清理、租约/费用不变、提交故障/丢回执/取消/实际硬终止和旧SDK拒绝分别验证；具体通过/未跑范围只见[验证](../validation/t15-abandonment.md)。模型与服务均为本地合成夹具，不是模型质量或云销毁证明。

# 运行原理

## 定义和执行

`Agent` 是可重建的定义：模型、instructions、工具、输出类型和限额。构造时不发出模型请求。注册到 Runtime 的 Agent 必须提供 `name` 与 `revision`；重启时用相同定义重建，不能把一个修订号悄悄改成另一套行为。

`Runtime` 持有存储、资源和执行任务。通过异步上下文打开和关闭；`ResourceBinding` 区分自有、借用和工厂资源。只有确认实际工作结束才释放相应资源，关闭超时可能返回 `ShutdownIncomplete`，需要宿主保留并继续收尾。

## 一次 Run 如何推进

接收输入与身份 → 保存确定模型输入和上下文清单 → 接收完整模型响应并保存工具计划 → 按权限执行工具 → 原子保存结果与检查点 → 继续模型或返回类型化结果。

`start` 接收后返回 Run 引用，`wait` 等待当前暂停或终态；`run` 组合两者。`stream` 观察相同内核，离开流或断开 HTTP 不会自动取消 Run。需要停止时调用 `cancel`；取消事实不证明已派发的外部操作未发生。

结果有 `completed`、`failed`、`suspended`、`cancelled` 四种。`suspended` 不是报错：读取当前 pending、检查原参数，再用同一 `request_id` 响应。重复相同响应返回原回执；冲突响应被拒绝。临时 `Agent.run` 不提供持久人工交互，遇到 ASK 会以 `interaction_unavailable` 失败，需改用显式 Runtime。

## 身份、工具和审批

宿主提供可信 `RunContext(tenant_id, user_id)`，不要让模型决定身份。默认工具策略允许 read，write/unknown 要求审批；显式 deny 和应用 Authorizer 的拒绝优先。未声明 effect 的 Python 工具按 unknown 处理。

外部 write/unknown 单次派发，不承诺外部 exactly-once。连接断开或回包丢失时，先核实实际效果，使用 `verify` 或提供有依据的核查响应；不能换 request_key 盲目重跑。SDK 恢复不是重建一个已死亡的 Python 进程。[审批与未知效果例子](examples.md)会检查独立服务中的实际写次数。

## 上下文与 Memory

旧历史按完整 Run 组选取；当前协议链、系统规则和输出 schema 受保护。工具结果可用带来源、版本与 hash 的有限预览；被排除的资料没有进入模型输入。`inspect` 提供当前页清单索引，`get_context` 查看具体调用的来源和取舍。token 预算使用启发式粗估，不是精确计费器。

`Memory()` 是配置，`MemoryStorage` 是存储后端，两者不同。配置 Memory 后导出 remember/recall/forget 三个事务工具；写入需要实际工具调用，普通聊天不自动保存。版本检查保护并发更新，forget 创建墓碑，不删除历史消息、模型输入或备份。检索按租户/用户/Agent 作用域隔离；首版是最近/精确键/规范化子串检索，没有语义向量检索。

## 查看、文件和恢复

`inspect/events/get_context/export_run` 默认只给元数据；正文另需 `view_content`，包括输入、代码、文件名和下载。当前授权在读取时复核。provider 原始协议项不显示为用户可读的思维链。

SQLite 持久状态和 LocalArtifacts 字节目录必须保持配对。沙箱产物在确认环境销毁后，才与工具成功结果共同发布为 ready。恢复查询和清理原环境、发布原暂存，不重新执行原代码。旧备份的恢复副本有持久屏障，不能立即接收新任务；[数据维护](maintenance.md)说明现有边界。

确切状态机与语义以[运行契约](../planning/runtime-contract.md)为准，接口以[API 契约](../planning/api-draft.md)和实际源码为准。

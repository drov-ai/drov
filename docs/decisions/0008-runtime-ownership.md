# ADR-0008：注册指纹与 Runtime 资源/任务所有权

- 日期：2026-10-13（Asia/Shanghai）
- 状态：T-04 实施决定；生命周期与私有调度原语已实现，Agent 执行/持久化取消/恢复仍由后续任务验收
- 关联：细化 ADR-0002/0004/0005/0006/0007，不增加首期能力范围；历史 P4 指纹不覆盖

## 原因

T-03 已保存 name/revision/config_hash，但尚无注册定义来证明恢复配置一致，也没有对象负责等待者退出后的任务、实际线程调用和关闭失败后的连接。只保留 asyncio Task，不能证明被取消的包装任务背后的调用已经停止。

## 决定

1. Runtime 构造时按 `(name, revision)` 建立唯一注册并校验类型/schema；缺身份或重复拒绝。生成算法 v1 的规范 JSON SHA-256 指纹，覆盖指令、模型适配器类型/能力/应用声明的 model_config、工具 revision/权限元数据/参数及返回 schema、输出 schema、Memory/上下文/限额和 required_dependencies。打开资源及恢复校验时重新计算，定义变更拒绝；注册事实只有指纹，不保存客户端内部属性、依赖对象或凭据。
2. Agent 增加可选 model_config 与 required_dependencies。model_config 只能放非秘密配置/版本引用，例如 model ID；任意函数体、闭包、远端行为和未声明的客户端配置无法由指纹证明一致，开发者仍须更新 revision。validate_record 校验记录 schema、原 revision、指纹和必要依赖，只是后续恢复的校验接点，不执行或扫描 Run。
3. Runtime 公开 open/aclose、异步上下文、registration、validate_record、storage/dependencies 与 active_slots；没有 start/run/wait/cancel/recover 的空壳。后续 T-06/T-09 接入状态和权限机制后才提供对应入口。任务调度/等待/取消控制是私有内核原语，测试直接验证其真实行为。
4. 未打开的 Storage 是资源描述，默认 Runtime 创建连接并拥有；已打开实例默认借用。ResourceBinding.borrowed/owned 显式覆盖，factory 在 open 时异步创建并转交所有权；借用存储必须已打开且可用。模型实例默认借用，owned_models 显式转交已注册实例。dependencies 中的现有对象默认借用，也可使用绑定或工厂；自有对象提供 aclose 或显式异步 close。
5. 装配顺序是存储、去重后的模型、按映射顺序的依赖；清理逆序，同一对象只关闭一次，冲突的所有权/关闭器拒绝。进程内资源引用登记阻止借用期间转交所有权及多个 Runtime 借用同一 Storage；普通借用模型/依赖可共享，宿主必须保证其可用性。Runtime 绑定一个事件循环、一次性使用，不假设客户端跨线程安全。
6. 活动槽位先预留再交给内核任务。Runtime 强引用实际任务和已经派发的底层 Future；等待使用 shield，显式取消设置标志并请求 Task 取消，取消后的 checkpoint 拒绝新派发。派发器必须在派发前 checkpoint，并在没有中间 await 的情况下登记真实调用；不能登记一个可取消包装器来代替实际线程 Future。任务结束且所有已登记调用均结束后才释放槽位；未交接的接收槽位也阻止关闭资源。即时任务工厂下同样先确立所有权再进入用户代码。
7. 关闭立即停止接收，请求活动任务取消，在同一宽限内等待槽位清空并逆序关闭资源。等待方取消不能打断装配/清理而丢弃刚创建的对象。关闭失败或超时保留剩余资源、关闭任务及存储锁，报 ShutdownIncomplete；修复故障/等待调用结束后可重试 aclose。关闭器失败时不继续关闭它可能依赖的较早资源。工厂在返回前的部分资源由工厂自行清理；工厂必须自行限定启动等待。禁止执行或生命周期回调关闭自己的 Runtime，避免等待自身。

## 替代与代价

- 序列化适配器/闭包或猜测全部属性容易泄露凭据，也不能固定函数行为；选择应用声明的配置引用加 revision，代价是应用负责准确声明和更新版本。
- 将任何传入实例一律视为自有会意外关闭应用连接；选择描述与已打开客户端的默认区分，保留显式绑定。默认行为在 Runtime 构造时确定，宿主不可在其后自行更改对象生命周期。
- 包装 Task 结束即释放更快，但会让实际线程继续使用已关闭连接；选择登记真实 Future 并保留槽位/锁。不能强杀 Python 线程，宿主在 ShutdownIncomplete 后仍需保留 Runtime 或按进程策略结束宿主。
- 停止关闭器后继续关闭存储会破坏依赖关系；选择在失败点保留剩余栈。宽限控制等待时间，无法保证某个自定义异步关闭器内不合作的同步代码可被抢占。

没有新增数据格式：RunRecord 的 config_hash 存算法 v1 的摘要，旧记录不匹配时拒绝恢复，不隐式迁移。Runtime 全局资源容量不属于 Agent 定义；每 Run 的有效限额仍由后续接收事务保存。真实模型/MCP/HTTP、权限、运行历史及副作用核查不由这项验证证明。

## 影响与证据

- [API](../planning/api-draft.md) 维护新增配置/生命周期入口；[架构](../planning/architecture.md) 维护资源归属；[运行契约](../planning/runtime-contract.md) 维护任务/取消/退出规则。
- [工程方案](../planning/engineering.md)、[质量计划](../planning/quality-plan.md)、X-02 同步 T-04 接点和验证范围。
- [T-04 验证](../validation/t04-runtime.md) 包含资源失败/取消/别名、恢复指纹、实际线程与 SQLite 锁实验；不替代 T-06/T-09/T-10。

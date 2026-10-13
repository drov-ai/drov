# ADR-0007：单所有者存储与事务接点

- 日期：2026-10-13
- 状态：T-03 实施决定；存储原语已实现，Runtime/审批/恢复调度仍待后续任务
- 关联：细化 ADR-0002/0004/0005/0006；API/运行契约 v3，历史 P4 指纹保持不变

## 原因

后续内核必须在外部调用之前知道状态、检查点、事件和操作事实是否已提交。aiosqlite 的队列只能串行 SQL，不能提供整个业务事务的隔离；取消等待也不能证明排队中的 COMMIT 没有完成。

## 决定

1. MemoryStorage 与 SQLiteStorage 共享 Transaction 操作。SQLite 用一个 records 表保存带类型的 JSON 记录和查询索引，metadata 保存格式/schema 与去重摘要密钥；不要求 SQLite JSON1 或 STRICT 表，保留 3.35 的最低版本。records 的 kind/归属/namespace/key 是复合主键，独立唯一索引限制 Run ID、同会话未结束 Run 和单 Run 活动交互。
2. 存储记录是完整内部事实，独立于 RunSnapshot/DurableEvent 的授权投影；不得直接用内部记录作 HTTP 响应。查询带 tenant/user，Session 与 Memory 的 namespace 分别是 session_id、agent_name。内部数据库格式和全局容量计数不属于用户业务查询。
3. 一个异步 gate 包住 BEGIN IMMEDIATE、所有读写和 COMMIT/ROLLBACK；普通读取也经同一 gate。事务归属于进入的 task，禁止嵌套、跨 task 使用和退出后复用。捕获写入异常不恢复提交资格，整个事务仍回滚。
4. start 先规范输入并查去重，再检查 Session/容量，原子写 Session、Run、初始检查点、accepted 事件和去重记录。去重范围是 tenant/user/session/request_key；原始键不保存，摘要使用存储本地密钥及 scope。规范请求保留输入、Agent name/revision、显式限额；省略和空限额都是无覆盖。重复请求返回接收时 RunRef，不刷新其版本。
5. save_step 要求旧版本匹配且只推进一个版本，状态/检查点/事件共同提交。事件使用每 Run 的连续序号；终态释放 Session。事务支持将操作/交互/回执/上下文记录和 Memory CAS 放入同一提交；receipt 不可修改，Memory 删除保留版本墓碑。它们是存储接点，不等于工具执行或人工响应引擎已实现。
6. SQL、连接建立和关闭都保留强引用并等待被取消的异步工作完成；COMMIT 成功后即使调用者收到取消，记录仍完整，重试通过去重取得原引用。提交报告错误不返回成功；连接停止接受新事务。回滚无法确认时保留连接和所有权，关闭报 ShutdownIncomplete，直到事务能被确认处理。
7. 数据库路径解析到真实路径；同进程登记加非阻塞 OS sidecar 锁。残留锁文件不代表占用；进程被终止后 OS 锁可重新取得。拒绝硬链接和非普通文件，事务前检测数据库文件替换。只支持本机文件，不能防止忽略锁的外部程序写库。
8. schema=1 只自动初始化真正空的数据库；非空未版本化库、未来版本、错误 metadata/索引拒绝打开且不迁移。初始化 DDL 与 metadata 同事务。迁移/清理/恢复旧备份 barrier 属 T-15，当前不提供假维护入口。
9. portalocker 的导入会探测临时目录，因此延迟到显式打开存储时加载；SDK 导入仍不得写文件、启动线程、打开连接或加载供应商客户端。

## 替代与代价

- 多表完全映射每个未来模块，需要提前冻结尚未实现的数据体；选择带类型记录和明确索引，代价是父子引用由共享事务操作检查，当前没有 SQL 外键级的全部引用关系保证。
- 每条 SQL 独立加锁会允许其他查询插入一半事务；选择整事务 gate，代价是长事务会阻塞同一连接。外部工具/模型不得在事务中调用。
- 取消等待后立即释放连接更快，但排队 COMMIT 可能继续；选择等待结果明确后再释放，不承诺强制终止 SQLite 工作线程。
- 仅以 PID/锁文件存在性判断所有权无法处理崩溃；选择 OS 锁加进程内登记。网络盘、硬链接和外部替换不在支持范围。

内存后端以写入副本提交/丢弃，不提供跨进程持久化。存储错误不是 Agent failed outcome；后续 Runtime 必须停止派发，不能伪造已经保存的结果。

## 影响与验证

- [架构](../planning/architecture.md)、[运行契约](../planning/runtime-contract.md)、[工程方案](../planning/engineering.md) 同步实际接点。
- [生成 SQL](../reference/storage-schema-v1.sql) 从代码 DDL 生成，仅供检查，不含可独立初始化的私有 metadata。
- [T-03 验证](../validation/t03-storage.md) 记录 B-02/03/09/10 的存储部分、真实锁/进程终止、提交故障和取消验证；不替代 T-09/T-10 的外部工具故障与运行恢复测试。

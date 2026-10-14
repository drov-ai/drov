# ADR-0021：沙箱故障边界保持原结果、限额与资源证据

日期：2026-10-15。状态：工作决定，T-10B 的实际验收范围只见 [progress](../planning/progress.md) 和 [专项验证](../validation/t10b-sandbox-faults.md)。沿用 ADR-0016/0019/0020，不改变公共调用、SQLite 扩展版本、旧记录或原执行绑定。

## 原因与选择

真实低 CPU 配额试验发现：监督进程的控制文件尚不存在时，首次读取会误判成执行失败。仅对可信读取程序明确报告的文件不存在返回“尚未就绪”，在原 control_seconds 内等待；一旦收到实际控制信息，仍严格核验。无效 JSON、非零退出、控制值不符仍拒绝。该等待不会重试代码、重建容器或放宽隔离。

真实销毁应答故障发现：recover 已共同发布原 operation.result/ready 文件，却在 tool.succeeded 事件中遗漏 result。恢复事件现在使用与正常成功相同的结果字段，保持原引用/hash；授权投影仍使用既有机制。

恢复还必须应用原 Run 的 max_tool_result_bytes。不能因正常路径在销毁失败处中断，就在 recover 发布过大的原结果。超过限额按 failed_known/output_unavailable 收尾，不发布 ready 输出、不重算；原观察/暂存仍保留容量与台账。

文件接收的私有 reserve/stage 工作线程若发生 OSError（如 ENOSPC），公开 Runtime 接收入口统一抛 PersistenceUnavailable；不将宿主路径/原始 OS 错误当作公共成功/结果，也不自动重写已开始阶段。私有字节层的异常保持原有内部契约，Run/ready/授予接收事务未提交。真实字节/清单和 SQLite 接收失败测试确认模型/容器均为0。

未知创建、停止或销毁不因查询暂时 absent 就伪造清理。无可靠创建身份的原租约继续占容量，关闭抛 ShutdownIncomplete 并保留 SQLite/字节资源所有权；硬终止释放 OS 锁不改变持久事实。已确认容器身份的丢销毁应答可通过原身份查询并确认不存在后收尾。

## 替代方案与代价

- 固定 sleep 或忽略控制检查：不能保证启动时序，也无法证明实际限制；不采用。
- 重跑原代码补输出：改变单次执行语义，可能产生额外效果；不采用。
- 将完整暂存当作 ready，或人工填造清理/引用：没有原事务和真实资源证据；不采用。
- 未完成/损坏/超限暂存不自动回收，仍计容量。完整维护/备份/回收在 T-15，不能把本轮测试收尾称为公共维护 API。

## 验证与平台

真实合成输入/宿主/网络哨兵、CPU/OOM/PID/文件风暴、路径/导出拒绝、客户端丢应答、SQLite trigger/ENOSPC、SDK/stream SIGKILL 屏障验证原资源和文件阶段。独立持久控制日志记录 create/exec/start 请求尝试及完成应答，不把开始尝试写成代码完成证据，也不把 SIGKILL 写成物理断电保证。

AMD64 分析镜像采用单独实际 PyPI wheel/哈希/许可清单，保留 ARM64 锁。Docker 专项 CI 必须有可用 daemon/固定镜像，缺配置直接失败；普通核心仍允许明确跳过 Docker。安装后从仓库外验证 core-only wheel 的标准库/分析镜像与 SDK/stream。实际远程结果见专项记录，配置和工作流存在不等于通过。

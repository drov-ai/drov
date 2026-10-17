# 数据保留、备份与导出

默认保留持久记录和文件，直到显式维护。SQLite/LocalArtifacts 不是加密存储，宿主负责私有权限、磁盘保护、备份副本和访问控制。不要把活跃数据库放在网络盘/同步盘，也不要在 Runtime 持锁时替换文件。

## 先用合成数据走完整流程

```sh
uv run --locked python examples/maintenance_offline.py
uv run --locked python examples/abandonment_offline.py
uv run --locked python examples/backup_offline.py
uv run --locked python examples/migration_offline.py
uv run --locked python examples/export_offline.py
```

每个例子新建临时数据，支持 `--stream`。POSIX 另验证字节目录，Windows 沿纯数据库分支。它们不操作你现有的库，也不自动解除恢复屏障。

## 维护原数据的顺序

1. 停止 Runtime 和实际 worker，确认外部执行/沙箱清理状态，保留未知事实。
2. 使用 `SQLiteMaintenance` 取得同一排他锁。先预览，核对 tenant/user/session、引用、租约和指纹。
3. 完整备份到新目录，校验版本/配对/ready 文件 hash。备份包含原始敏感内容，不等于脱敏导出。
4. 显式提交预览计划；预览过期或范围变化时重新预览，不猜测修补指纹。
5. 若发生中断，按持久台账恢复原维护操作；pending 回收期间普通 Runtime 打开会拒绝。

CLI 入口是 `python -m drov.storage.maintenance --help`，参数和 API 见[实际签名表](api-signatures.md)。不在本文提供可直接删除任意用户库的默认命令。

## 几种操作的区别

| 操作 | 实际效果与限制 |
| --- | --- |
| Session 逻辑清理 | 整会话记录与可选 Memory scope；旧 Session/请求键返回 Gone，字节回收量为零 |
| 显式字节回收 | 无引用/活动租约的有效阶段与显式选择的过期未消费上传；先保存 pending 库存，完成后记 done |
| data_abandoned | 放弃无法推进的 Run，保留原未知操作/清理义务与不可恢复原因；不核销费用或证明外部未执行 |
| 一致备份/恢复 | SQLite backup 加配对字节校验；新目录副本发布前设置 recovery barrier，不覆盖原库 |
| 兼容迁移 | 具名历史 v1 同义字段回填，先备份再原子回执；不支持任意未来 schema |
| export_run | 默认元数据，正文/逐文件分别授权，应用 redactor 只改副本；不是可恢复库或逐字模型重放 |

共享文件只有最后合法引用清理、且无活动/未知租约时才可回收。TTL 不删除已消费输入；清理逻辑行不等于 SQLite 页压缩、磁盘安全擦除或备份/供应商删除。

## 恢复屏障与兼容

旧备份可能缺失备份后的请求键、写入与执行事实。恢复副本必须先核实并补齐这些事实；当前**没有通用自动或 force 解除屏障接口**，应用专用重建/解锁尚未提供。查看与离线维护可用，新接收和外部派发继续阻断。

普通打开不自动迁移；启用 retention/reclamation/abandonment/recovery/migration 等独立扩展后，旧 SDK 会拒绝。降级前保存完整副本，不能把旧 SDK 指向新扩展库绕过拒绝。准确数据规则和声明以[维护发布策略](../planning/release-policy.md)及[运行契约](../planning/runtime-contract.md)为准。

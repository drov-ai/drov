# ADR-0031：已知兼容v1字段的显式离线迁移

日期：2026-10-16（Asia/Shanghai）。承接T-15成对备份；状态/验证见[progress](../planning/progress.md)/[记录](../validation/t15-migration-export.md)。

## 原因与决定

仓库已有真实早期v1记录：T-05 Run尚无artifact_inputs/artifact_binding，ToolOperation尚无effect_resolution/evidence/memory_sources。当前读取器已按兼容默认值解释它们，需要显式、可恢复的实际记录变换与故障证据；没有发布过可据此宣称支持的v0。保留core/user_version/record_schema=1以及所有原DDL，迁移profile固定为v1-compatible-defaults。

SQLiteMaintenance.preview_migration在原排他锁下验证原记录/ready文件并给无正文的完整HMAC计划、实际变更数、未结束/未知操作数。migrate必须重比对完整计划，先完成已隔离的成对备份，再同一事务回填已知默认字段、写migration_schema=1/migration_receipts及完整性检查。Run/operation的版本、状态、参数、结果、事件、请求键及原密钥/接收摘要、租约/费用/字节保持原义；使用当前类型解码前后逐项比较语义。无变更拒绝执行，不给普通新库预建迁移表。未来版本/字段、错误索引或不认识的profile保持拒绝，不猜测恢复游标或工具执行状态。

备份快照ID与完整原计划的HMAC绑定不可变回执。提交后丢应答/取消，重开后同计划只返回原回执，不新建备份/事件；其他计划变化必须重新预览。事务失败/硬终止后全成或回滚；备份已完成但迁移未提交时，原备份保留，重试使用另一个新备份目标，不覆盖。活动运行只做同义格式回填，原revision/配置/预算和unknown仍受原恢复规则；迁移恢复副本不解除全存储屏障。

CLI提供preview-migration/migrate（完整plan-file、新destination用于备份），无HTTP自动升级。原SDK拒绝新增迁移扩展，未知migration版本/坏回执拒绝。profile不是通用未来schema转换器，也不声称所有旧开发提交/未来不兼容版本都可迁移；需要新变换时增加具名规则、真实历史夹具和兼容决定。

## 替代方案与代价

不为验收制造一个已发布v0，不自动改写所有读取到的旧记录，不改变schema编号却暗改状态含义。选择实际早期v1兼容字段回填，代价是显式全库/文件校验、完整备份、独立回执扩展和升级后的旧SDK拒绝。原始旧记录和当前解释保持可对照，未来不兼容迁移另行实现。

## 验证

[历史夹具](../../tests/data/storage-t05-v1.json)由d84c66fb7df36395d679c1dd4b45a99e5bbc23f1开发SDK实际写入合成Run/事件/派发记录，来源与内部合成HMAC种子见[夹具说明](../../tests/data/README.md)。测试验证真实变换、原去重/事件/unknown不变、原始备份、未来版本拒绝、SQL故障/丢提交回执、实际进程kill/回滚、取消和CLI。当前SDK制造的省略字段例子另明确标为合成旧字段包络；不当作真实历史writer证明。最终结果只见本轮记录。

兼容补充：离线维护快照检查每个原记录包络为精确整数schema_version=1，未来/boolean/损坏包络在变换和删除前拒绝；未知当前版事件类型可由观察层显示安全元数据，未来持久记录版本仍由原读取器拒绝，不靠迁移猜测。

备份/迁移同时校验框架持久RunItem协议包络及已保存ContextManifest/ModelRequest/ModelResponse；未知内层协议版本也拒绝认证或变换，任意业务结果字典不按其字段名当作框架版本。

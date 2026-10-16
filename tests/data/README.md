# 历史存储夹具

`storage-t05-v1.json`只含合成数据，由仓库T-05开发提交`d84c66fb7df36395d679c1dd4b45a99e5bbc23f1`的实际SDK写出；不是公开发布版本或真实用户库。2026-10-16从该Git提交archive构建wheel，29个SDK载荷逐字与该提交比较，wheel SHA-256为`3b95f09fdc40e93b0a4498ae1306cdb4157f063dd251b647fbdb3fd05ae18f58`。

在隔离载入旧wheel的合成临时库中，先固定内部去重HMAC测试种子（`11`重复32字节，非供应商凭据），再通过旧StartRequest/SQLiteStorage/OperationJournal写两个Run、一条prepared→dispatched→unknown的合成操作及真实旧格式事件/去重记录。没有实际外部写入、模型或云调用。捕获原DDL/metadata/行与来源，不用新模型反向生成旧payload；Run/operation中新字段缺失是该历史writer的实际输出。

测试从JSON原样建立临时SQLite，检查迁移前后当前类型解释、原版本/事件/摘要、未知派发和同键返回不变，原始副本保留。迁移仅支持具名v1兼容回填，未来版本拒绝；不据此宣称支持全部历史提交。实际生成与首次非法事件枚举修正日志保存在本地验证目录，来源/边界见docs/validation/t15-migration-export.md。

# T-03 存储与原子边界验证

- 日期：2026-10-13（Asia/Shanghai）
- 开发版本：0.1.0a0；准确代码与 GitHub CI 状态见当前 [progress](../planning/progress.md) 和提交记录
- 契约：P4 v3 + [ADR-0007](../decisions/0007-transactional-storage.md)；旧 P4 baseline JSON 未覆盖
- 本地环境：macOS arm64，Python 3.11.9 / SQLite 3.45.1，Python 3.14.3 / SQLite 3.52.0；依赖沿用 uv.lock，未升级版本

## 验收证据

| T-03 要求 | 实际验证 |
| --- | --- |
| 内存/SQLite 共同规则 | test_storage.py 对两后端执行相同契约：start、CAS、检查点/事件、Session 绑定及终态释放 |
| 去重先于忙/容量 | 同键并发 20 次只建一 Run/一输入事实；同规范重试返回旧版本引用，输入/revision/Agent/限额冲突拒绝；空限额与省略一致 |
| 归属隔离 | 同 tenant/user/session/key 不混合；不同用户/tenant 的 Run、事件、检查点不可读；Memory 按 tenant/user/Agent 查询 |
| 事务全成或全败 | body 失败、写到一半、捕获 mutation 错误、提交前/后报告错误；不返回虚假成功。状态/检查点/事件同版本，20 次 CAS 只有一次推进 |
| Memory 原子接点 | Memory CAS、操作结果可同事务；失败全部回滚，删除版本墓碑可读，重建不能从版本 0 开始；不是 Memory 工具已实现 |
| 交互/回执接点 | 最多一个活动请求的存储唯一约束、版本更新与不可修改回执；响应授权/期限/消费工作流仍在 T-09 |
| 提交取消 | SQL await 边界受控取消两次，读取及所有权仍等待 COMMIT；真实 SQLite 外部写锁阻塞 BEGIN 时取消后等待处理再回滚 |
| 不明回滚 | COMMIT/ROLLBACK 故障阻止后续事务，关闭报 ShutdownIncomplete，另一实例不能取得锁；解除故障后可确认回滚和关闭 |
| 本机文件/锁 | 同进程重复、软链接别名、硬链接拒绝、残留 sidecar、文件替换；初次文件 POSIX 权限 0600 |
| 真实进程 | 两个独立 Python 进程竞争锁；正常关闭与硬终止后重开；未提交记录全部消失，已提交状态/Memory/结果/事件保留且去重不新建 |
| 版本/格式 | 验证实际 SQLite 版本与四项 PRAGMA；非空 schema=0/未来 schema/不兼容 record metadata 拒绝且数据库字节不改；载荷与索引归属/版本不符不返回 |
| 分页 | 相同创建时间下 Session/Run keyset 分页无重复/遗漏；事件 seq 增序，超过末尾拒绝；HTTP opaque 游标待 T-13 |

现有 116 项类型/协议测试加本轮 52 项，共 168 项；两个本地解释器的完整测试均通过。记录只能支持上述存储结论，C-11 的 Runtime 恢复、B-09 的模型工具调用、B-10 的 HTTP/动态权限仍未实现。

## 检查命令

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/generate_schemas.py --check

UV_PROJECT_ENVIRONMENT=.drov/check-py314 uv run --locked --python /opt/homebrew/bin/python3 pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 uv run --locked --python /opt/homebrew/bin/python3 python scripts/generate_schemas.py --check

uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
```

维护者本机 uv 为 `.drov/tooling/bin/uv` 0.13.0，3.14 环境明确指定解释器，不重置默认 3.11。构建检查包括源码 wheel/sdist、sdist 重建内容比较、两个干净核心环境安装和禁用导入副作用；不上传包。

31 份 Markdown、215 个相对链接、11 个 Python 片段语法、18 个任务/23 条依赖、schema 漂移、55 个品牌文件摘要及原 P4 baseline 完整性已检查。品牌源文件保留本地并排除 Git，未验证来源前不随代码同步。

## GitHub CI

实现提交：[751ff55](https://github.com/drov-ai/drov/commit/751ff55629c8ac8d97b20973b741ab0b373e97cf)。[CI 38345105142](https://github.com/drov-ai/drov/actions/runs/38345105142) 已完成，10 个 job 全部 success：quality、Linux 3.11/3.12/3.13/3.14、macOS 3.11/3.14、Windows 3.11/3.14、Python 3.15 非阻塞探测。标准矩阵执行测试、构建/干净核心安装及 all-extras 导入检查；没有模型 Key 或 live 调用。后续纯交接文档提交不改变已验证代码。

## 实际发现的修正

- portalocker 导入调用 tempfile.gettempdir，触发临时文件探测；延迟到 FileOwnership.acquire，保留原包导入检查，未放宽检查器。
- Python 3.14 检出了测试中的 sqlite3 连接未关闭；连接上下文只管理事务，夹具补显式 close，完整测试退出成功，不忽略 ResourceWarning。
- 写入异常被调用者捕获后可能留下半个事务；mutation 失败标记取消整个提交资格，并有回归用例。
- 载荷与索引 owner/version、检查点与状态版本不符时必须拒绝；不能用索引命中证明完整记录可信。
- keyset 使用统一微秒精度，调用者传 datetime，不能让省略小数的时间字符串造成漏页。

## 重要限制

没有 Agent 循环、模型调用、外部工具执行、HTTP、工作台、迁移/备份/清理入口。进程实验是存储故障，不是外部副作用 exactly-once 证明。所有权锁只协调 Drov；不支持网络/同步盘、硬链接或活跃文件替换。

COMMIT 完成但调用者被取消，或者提交后的异常使调用者不知道结果，必须查原去重键/状态；不能据此再次执行外部写入。不明事务停止使用并保留所有权，不承诺可杀死 SQLite 线程。

Windows 跳过“替换已打开文件”的 POSIX 回归用例，因为该操作被 Windows 文件共享规则限制；其他实际平台/版本结果以 GitHub CI 记录为准。Python 3.15 为非阻塞探测，不算支持承诺。当前 record_schema=1 的未来/旧版本拒绝已验证，真正显式迁移和旧备份 barrier 在 T-15。

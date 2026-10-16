# T-15 第三批：显式放弃运行验证

日期：2026-10-16（Asia/Shanghai）。从同一Local `/Users/yanlong/projects/drov` 接续；第二批代码ffec4d12ed787dcf1e6d3b8a686677eefd25473e核心11/Docker4 jobs全部success，验收文档6355c14已推送，实际状态/日志见[回收记录](t15-reclamation.md)。用户在验收收尾中要求继续未完成部分，因此接续本批，没有跳到T-16。

## 范围

按[ADR-0029](../decisions/0029-offline-run-abandonment.md)交付可信离线预览/放弃/最小事实查询，failed/data_abandoned与不可恢复标记、原未知操作/租约/费用保留、prepared未执行、当前交互关闭、完整预览的幂等回执和匹配事实后允许Session正文清理。首次实际放弃启用独立abandonment_schema=1，原所有DDL/版本保持。当前还不交付一致备份/恢复/barrier、实际迁移和授权导出，T-15整卡仍进行中。

只删除临时合成库/文件；没有读取真实Key/私有env、调用付费模型或云、修改原用户数据库/字节/租约/云台账/品牌，未公开发布或部署。脚本合成的未知外部操作和费用不是实际供应商状态。

## 实际失败与修正

- 修改后原维护/回收/HTTP80项通过（6.21s，`.drov/t15-abandonment-prerequisite.log`）。首轮新测试19通过/1失败（0.91s，`t15-abandonment-first.log`）：丢提交回执后原SQLite按既定规则poison，测试错误地在原实例重试；改为断言原实例继续拒绝、重开后按原计划取得相同回执，没有绕过保护或修改事务规则。第二轮20项通过（`second.log`）。
- 增加租约/真实SDK审批/提交取消/硬终止/CLI后，第三轮22通过/4失败（0.82s，`third.log`）：先前自动修正移除未使用asyncio导入，新测试增加调用后未补回；补导入后26项通过（1.68s，`fourth.log`）。静态检查还修正夹具动态Literal构造与SQLiteDriver边界，未放宽mypy。
- 再增加原未知阶段→放弃仍保护→显式正文清理→字节回收与HTTP回归后，维护/放弃/回收/HTTP108项通过（7.84s，`fifth.log`）；两解释器再加SQLite进程/Runtime故障各134项通过（13.01/14.00s，`target{311,314}.log`）。后续增加事实显式终态标记和SDK/stream合成例的最终检查点另验，不将早先结果当新源码全库。

## 最终检查点

代码/测试检查点 **a4c8c7a95eea1dfe846f3f297b97aec3b9435005** 已推送；本地和该精确SHA平台检查通过，本子范围已验收，T-15整卡仍进行中。

- 本机Darwin ARM64、Python3.11.9/3.14.3、uv0.13.0。最终3.14含真实Docker全库 **1254通过/59skip（328.21s，1313项）**，`.drov/t15-abandonment-full314.log`；DROV_E2B_TEST_CONFIG显式为空，59项是真实E2B未授权，不是云验证成功。最终新增28项（27放弃/1HTTP），原两解释器134专项为显式终态字段/例子增加前的检查点，不冒充最终3.11全库。
- 两解释器wheel/sdist重建、四套core-only安装、POSIX八个例子的normal/stream通过（`.drov/t15-abandonment-package{311,314}.log`）。新增放弃例实际暂停审批→关闭Runtime→离线放弃→拒绝原批准/恢复→清理正文→原事实仍在，model=1/tool=0。两套独立server安装后三场景loopback HTTP/SSE与五个工作台资产通过（`installed{311,314}.log`）。
- 两套构件完全一致，全部85个SDK/资源载荷逐字等于本次源码；wheel **5e51bb6861f06ae1bccc497c60c056a582e9d045ae395d9bd90e1ad4f9ae4264**、sdist **06c2fdf745b38e9a6aef3e9ce7ad9ef1c0818c2f1a1472f1e4f464e90a1b7a5e**。Twine strict通过（`t15-abandonment-twine.log`）。
- 实际隔离载入历史ffec4d1开发wheel，84个载荷逐字等于该历史源码，wheel SHA256 **9b3c1ac986e275e922ad81b7974c1eba02b2a21db32cf54596887e87a2073bfa**；能读原库，拒绝新增abandonment扩展库，两库字节均未变（`.drov/t15-abandonment-compat.log`）。这不是公开版本迁移证明。
- Ruff、251文件格式、严格mypy169文件、生成schema/OpenAPI、空白检查通过；原core/Artifact/Sandbox/Upload/retention/reclamation DDL、版本、stage/header及容量定义15项AST比较保持（`t15-abandonment-original-definitions.json`）。82份Markdown/714相对链接/16段Python AST、品牌拷贝与受保护原文件、跟踪变更凭据模式检查通过（`t15-abandonment-doc-check.json`）；静态检查不等于任意数据安全保证。

最终[核心38858719899](https://github.com/drov-ai/drov/actions/runs/38858719899)全部11个job、[Docker38858719894](https://github.com/drov-ai/drov/actions/runs/38858719894)全部4个job completed/success，两套元数据headSha均为 **a4c8c7a95eea1dfe846f3f297b97aec3b9435005**，完整证据`.drov/t15-abandonment-{core,docker}-ci.{json,log}`。Linux/macOS3.11核心932通过/160skip，Linux3.12–3.14/macOS3.14为934通过/158skip；Windows3.11为778通过/314skip，3.14为780通过/312skip。POSIX文件分支在Windows适用跳过，不能称Windows文件已支持；八套server/MCP/安装/extras步骤均success，HTTP专项POSIX75通过、Windows61通过/14skip。Docker各102通过/3适用skip，3.11另有24个实际Docker浏览器通过；不把核心skip计为专项通过。Python3.15仍advisory，未扩大正式支持矩阵。

**第三批显式放弃与最小未知事实已验收；T-15整卡仍未关闭。** 验收后同轮接续一致备份/屏障，另见[第四批记录](t15-backup.md)；该增量不冒用a4c8c7a的CI/构件结果。原数据、环境、租约/云台账和品牌保留，本批没有付费模型/云调用。接续任务、资源与会话建议只维护在[progress](../planning/progress.md)。

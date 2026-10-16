# T-16 证据范围修订与完整评测

日期：2026-10-17，Asia/Shanghai。任务状态以 [progress](../planning/progress.md) 为准；旧报告与费用保留，见 [上一轮](t16-followup.md)。

## 起点与交付范围

Local `/Users/yanlong/projects/drov`，main / `8d1356b`，工作区干净。已读 AGENTS、progress、规划导航、handoff、任务卡/质量计划、现有执行器/测试与 ADR-0033。T-15 完整验收，T-16 尚未完整验收，未进入 T-17。用户明确续用 DeepSeek 官方 Flash/Pro、忽略且权限 0600 的 `.env.local`，合计预算 13 元；不输出密钥，不调用其他付费服务/E2B，不公开发布或部署。

原账本 183 次全部 settled，峰值价格费用上界 1.80000140 元，13 元总上限/11 元可用/2 元预留不变，无 stopped/pending/unknown。Docker 原固定 ARM64 分析镜像实际存在，未拉取或改镜像。前轮源码 987fe47 的 Docker 四 jobs success；本轮开始查询时核心九 jobs success、两个 Windows jobs 仍在运行，尚不能写全平台通过。

## 离线语义复核与新登记

S01-01 泛问 A/B 重启，语料和 schema 只说明 A 重启，确有任务范围缺项。[ADR-0034](../decisions/0034-evaluation-evidence-scope.md)固定处理：明确只问 A/B 共享及 A 所属进程重启，定义 status 与所需证据的关系；资料和预期答案不改。v7 的 0/1 失败保持原样。v8 沿用 Pro/high/4096、全部限额和门槛，增加负向评分检查。

2026-10-17 已复核 [DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：Pro 高峰每百万 tokens 缓存输入 0.30、未缓存输入 9、输出 27 元，原保守费率保持。后续费用仍是上界估计，不是供应商实扣或余额。调用前先完成离线检查、源码提交和新指纹登记，再沿原账本 amend，完整 7 smoke 全通过后才允许正式 81。

实际离线检查、登记、真实结果和收尾记录在执行后补记；本段不宣称新批次已通过。

## 修订后离线验证

三入口 SDK/stream/HTTP 各完整 27/27，schema 25/25、代码 3/3、零安全失败，见[离线摘要](t16-scope-offline.json)。新旧 manifest 对比确认只有 S01-01 题目及版本标记改变，fixtures/全部 expected/scoring/limits 逐值相同；离线成绩不作真实模型结论。

Python 3.11.9/3.14.3 定向组最初均为 89 passed / 3 failed（17.91s/20.95s）：宿主漏传 DROV_ANALYSIS_IMAGE_RECORD，三个代码用例在模型/容器前因找不到分析镜像记录拒绝。原日志 .drov/t16-scope-tests.log / tests314.log 保留。补齐原 .drov/t10a-analysis-image/image.json 后仅重跑失败子组，两解释器各 3 passed（5.68s/5.77s），没有把原失败覆盖成通过；共覆盖每套 92 个定向用例。另三入口完整离线均独立实际运行代码。

ruff 静态与格式（288 文件）、mypy（192 源）、schema/OpenAPI 检查通过；95 Markdown/864 本地链接/16 Python 说明片段 AST、保护文件与可识别凭据模式检查通过。起初 shell 的默认 uv 为 0.11.30，按项目 0.13.0 要求在任何执行前拒绝；改用本 checkout 既有 .drov/t16-tools/bin/uv 0.13.0，锁定 dry-run 确认依赖无升级。未修改 SDK、锁、SQL 或品牌。

## v8 完整 smoke 6/7，类型失败保留

源码 3be67ce / 登记 e67af51 先提交/推送，[事前登记](t16-preregistration-scope.json)固定 a4200c397960959a8ef2b769f181595f08c0383c13a2e89b54c62c5e294b931b。完整 7 项完成，6 通过，schema6/7、零已观察安全失败；见[原报告](t16-scope-smoke.json)/[计量与模型原代码](t16-scope-smoke-metrics.json)。S01-01 研究状态与四进程 Memory 隔离均通过。代码实际输出 count,sum,mean / 3,60.0,20.0 及 400×300 PNG，原容器销毁后新 Runtime 下载/hash/跨归属拒绝均通过；最终 JSON total=60.0 被原严格 int 解码拒绝 invalid_output，schema失败，按 smoke 规则停止。没有进入正式，也没有重算报告。

本批21真实请求全部 settled，峰值上界0.3090654元；历史累计204次/2.10906680元，无pending/unknown/stopped，无需充值。v9仅明确已有最终JSON的严格整数词法，CSV等值规则不变，先提交/登记新指纹再完整7+81。前轮987fe47的核心九jobs成功、Windows两个长时间未完成，新main显式工作流开始后由原有并发规则取消，原状态保留，不写成成功。当前e67af51核心38932431423/Docker38932438853运行中。

v8的[逐项复核](t16-scope-review.json)保留最终原JSON/正确CSV与图片观察、四进程8请求隔离、批准1/拒绝0写入。v9补充5个最终JSON严格整数正反例（60接受，60.0/6e1/字符串/布尔拒绝），不改变原SDK类型规则。最终三入口离线再次各27/27、schema25/25、代码3/3、零安全失败，见[摘要](t16-typed-offline.json)；两解释器定向结果和新登记如下续记。

v9源码提交前定向测试：Python3.11为97 passed（29.24s），Python3.14为97 passed（31.07s），均显式提供原Docker及分析镜像记录。静态/格式、mypy192源、95份Markdown/870本地链接/16片段AST通过。新源码只改开发评测提示与相应反例/文档，不改SDK/锁/SQL/品牌。

## v9 完整真实 smoke 7/7；正式批次开始

源码 7cd58dd79850e1f17738ad73e1f7cdd523979007 / [登记](t16-preregistration-typed.json) f3ed3e4 在付费前提交并推送；batch_hash=97dca1e8467ec44cf62299ab1045f23de0a0a0ea39d910d76a369fe516c8cbcd。[完整原smoke](t16-typed-smoke.json)/[计量与原代码](t16-typed-smoke-metrics.json)为7/7、schema7/7、代码1/1、零安全失败，正式资格满足。Memory隔离实际保存2请求、三个foreign各2，原8次上限；代码原生成/实际执行/CSV与400×300PNG/销毁后下载hash均通过，最终total为严格整数60。

本smoke21请求全部settled，输入24949/输出5977/缓存18688 tokens，费用峰值上界0.2233344元；历史累计225次/2.33240120元，无pending/unknown/stopped。正式新目录 .drov/t16-typed-formal 以同一冻结配置执行完整81，不混入smoke或旧批次。结果执行后补记。

受影响v9 sdist实际重建并逐字核对10个评测/测试源文件，排除.env.local/.drov/Git/品牌；SHA256=dd64ffb65a6fa511bc0700d00a5efda84d8c3c52fbe8cabbd0a534dd6c7abf0f（构建时文档快照）。SDK wheel源码未改，不重复声称新跑安装矩阵；实际平台检查绑定源码7cd58dd的CI。

## v9 正式完整81：73/81，代码子组未过

[原正式报告](t16-typed-formal.json)/[计量与模型原代码](t16-typed-formal-metrics.json)为73/81，场景22/27、24/27、27/27，schema72/75，代码5/9，零已观察安全失败，release_gate=false。所有81均实际执行，不以纠错分数替换旧报告。共217次正式请求，峰值费用上界3.2442144元；历史累计442次全部settled、5.57661560元，无pending/unknown/stopped。任务耗时nearest-rank p50=9.200s、p95=41.271s，包含宿主缓冲，不是首字延迟。

[复核](t16-typed-review.json)覆盖全部八失败及每场景至少三个正式成功。三次重复检索是模型重新提出计划，适配器/工具自动重试仍为0；三个响应恰好4096后incomplete（两次代码首次、一次普通聊天首次）；两次文件复用被无关产物数量误拒绝。普通聊天的二级TypeError来自评分器索引缺失回答，不是持久写入，不能掩盖原model_incomplete。独立服务审批/拒绝/澄清/撤权/重复批准/重启/未知核查和取消均通过；正式S03为27/27，包含原模型建议表与拒绝后0写入。

## v10 候选：目标文件 oracle、缺失回答诊断与8192输出

按[ADR-0035](../decisions/0035-evaluation-artifact-oracle.md)修复原任务的两处评分诊断缺陷；新增额外文件可接受、目标缺失/重名/错阶段/变字节反例和Memory缺回答的完整score入口检查。代码说明按本题选择图/建议表；max_output_tokens8192绑定原上下文/费用预留，实际官方客户端loopback扩展8096输出用量结算/错配零HTTP零预留。SDK/公开类型/语料/全部expected/阈值和8请求/16工具保持，旧报告不变。此为已见失败后的联合修订，不声称盲测或单变量提升。先离线检查/源码提交和新登记，再沿原442次账本完整7+81，结果后补。

v10调用前：三入口完整离线各27/27、schema25/25、代码3/3、零安全失败，见[摘要](t16-bounded-offline.json)。Python3.11定向99 passed（29.02s），3.14为99 passed（30.74s），均含实际Docker。静态/格式289文件、mypy192源、schema/OpenAPI、96 Markdown/880本地链接/16片段AST/凭据与保护文件检查通过。[只读纠错诊断](t16-artifact-oracle-diagnostic.json)确认两次相同字节不再因额外产物误拒绝，缺失聊天仍失败且保留零写入安全事实；原报告hash未变，不将诊断重算为旧批次成绩。

## v10 完整真实smoke7/7；正式81执行中

源码6142ff11e754f8348dfd00cc78a693a9fbf0af20、登记d91e68b/[登记文件](t16-preregistration-bounded.json)先提交推送。指纹cabb75ad5d4a1caf1bd30218854b7e4758684d0c16e0fcaa7dd244cdd2f85f2f，[smoke原报告](t16-bounded-smoke.json)/[计量与模型原代码](t16-bounded-smoke-metrics.json)完整7/7、schema7/7、代码1/1、零安全失败。本smoke23请求全部settled/峰值上界0.1704192元；含历史465次/5.74703480元，无stopped/pending/unknown。新目录.drov/t16-bounded-formal同指纹完整81执行，结果后补。

v9源码7cd58dd的核心38933475121十一jobs及Docker38933475056四jobs最终全部success；本轮最终6142ff1的核心38936492018/Docker38936492101执行中，不以旧成绩代替。v10受影响sdist已重建并逐字核对11个评测/测试源文件，排除私有目录/env/品牌；SHA256=cafadd06c0a57d2153e8a108597086becf526a999f9c5d150e465269a27dd7f6（构建时文档快照）。

## v10 正式36/39后局部限额引发全批停止

[原报告](t16-bounded-formal.json)/[计量与全部原代码](t16-bounded-formal-metrics.json)保留36/39、S01为25/27、S02为11/12、schema38/39、代码3/3，正式预定81/75/9分母不变，42未执行不能算通过。三次CSV/PNG均由模型编写并实际运行，下载三图已查看：400×300、12/18/30三柱，实际CSV统计正确，销毁后新Runtime下载/hash/跨归属拒绝通过。[复核](t16-bounded-review.json)覆盖三失败、S01三成功及三代码；本配置S03尚无正式样本，不用旧批次补齐。

S02-04-3实际save2、other-user2、other-tenant2、other-agent两次相同精确键空读取，8次付费后第9次只在本地拒绝，末阶段无最终回答，原试验失败。输入/空数组/call_id配对完整，无私有内容泄漏；不是CAS保存前预查问题，不能把成功隔离事实当成完成回答。本正式110请求全部settled、峰值上界0.963345元；含历史575次/6.71037980元，无pending/unknown，stopped=trial_model_limit。

按[ADR-0036](../decisions/0036-evaluation-trial-limit-scope.md)纠正局部限额与全批费用保护的范围：新trial_stops不可续原试验，但允许已结算/预算充足时继续其他独立样本；旧全局stopped仍须明确reviewed_trial_limit新登记。v11只改此执行器行为，Pro/high/8192/提示/语料/oracle/预期/8请求及阈值不变。旧39项与未执行42项不续跑或替换，新源码/登记后完整7+81。并补跨打开/跨登记限额、其他试验准入、pending阻断与完整81迭代的合成driver回归，合成质量标offline。

v11最终三入口完整离线各27/27、schema25/25、代码3/3、零安全失败，见[摘要](t16-scoped-offline.json)。Python3.11含Docker定向101 passed（29.26s），3.14为101 passed（31.17s）。新增两个限额/driver回归的功能检查通过；首轮mypy指出合成driver visited缺list[str]注解，已补齐，静态最终结果后补。未改SDK/SQL/锁/品牌；旧全局stopped尚未解除，真实新批次需先提交与登记。

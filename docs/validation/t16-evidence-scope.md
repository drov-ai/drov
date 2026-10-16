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

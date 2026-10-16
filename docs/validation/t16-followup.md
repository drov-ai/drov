# T-16 接续评测：Memory 精确键读取澄清

日期：2026-10-16，Asia/Shanghai。状态与下一步以 [progress](../planning/progress.md) 为准；历史失败保留在 [原记录](t16-evaluation.md)，不覆盖、不重算。

## 起点与离线复核

Local `/Users/yanlong/projects/drov`，main / `94830fe`，起点工作区干净、与 origin/main 同步。已读 AGENTS、progress、规划导航和 handoff；T-15 完整验收无缺项，接续 T-16，不进入 T-17。用户明确延续 DeepSeek 官方 Flash/Pro、私有 `.env.local` 配置与合计 13 元授权；不调用 E2B 或其他付费服务，不发布或部署。

原账本 `.drov/t16-model-ledger.json`：44 个 attempt 全部 settled，保守高峰费用上界 0.24583100 元，原总上限 13 元 / 可用 11 元 / 余量 2 元，stopped=trial_model_limit。保留全部历史费用、计数、登记、输出目录与数据库。不是供应商账单或余额。

[保存输入审计](t16-memory-isolation-audit.json)逐项核对 Pro/high S02-04：save 两次；other-user 两次（同一计划内精确键和子串两工具）；other-tenant 三次（精确键空结果后另查子串）；other-agent 第一次精确键空结果，第二次请求在本地预留时被拒绝。共 8 次真正模型请求；空数组结果、原指令和 call_id 结果均保留。未发现本地适配器丢输入的证据，不修改 SDK 或自动 Memory 空状态语义，也不推断供应商内部原因。

## 事前登记的改变

本轮明确续做后，只追加一次 S-02 操作指令澄清：已有注入记录直接使用；无记录需读时只作一次 exact-key recall（query=null）；成功空数组是该作用域没有该键，不再改子串重查；修改成功需真实工具成功。保持 Pro/high/2048、语料/期望/评分及每任务 8 模型请求 / 16 工具派发不变。它是见过旧失败后的自适应接续，不是盲测，也不将新结果替换旧失败。

调用前提交源码与配置、保存新指纹/登记；离线完整三入口验证后，以 reviewed_trial_limit=True 明确登记原停止的复核，保留旧账本。新目录完整 7 smoke 全部通过才允许同指纹正式 81；若失败保留并停止，不自动继续改提示抽样。

价格于本轮复核 [DeepSeek 官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：Pro 高峰缓存输入 0.30 / 未缓存输入 9 / 输出 27 元每百万 tokens，原保守价格保持。滚动 model ID 不代表固定供应商快照。密钥只由宿主在内存加载需要的一项，不打印/提交。

## 本轮验证与结果

开始前定向检查：Python 3.11，`pytest tests/test_evaluation.py tests/test_deepseek_responses.py -q`：67 passed / 3 未显式配置 Docker 的 skip（15.06s）。这不作为 Docker 通过；随后完整三入口均提供原固定 Docker 配置。evals 的 ruff 静态/格式通过。后续真实结果与正式复核在完成后补记。

新配置三入口离线完整：SDK/stream/HTTP 各 27/27，schema 25/25，代码 3/3，零安全失败，见[摘要](t16-followup-offline.json)。全部 release_gate=false；只验证结构和真实 Docker 的脚本计划，不当作真实模型质量。mypy 192 源通过；93 份 Markdown、838 本地链接、16 Python 说明片段 AST 通过，保护文件变化/可识别凭据模式均 0。

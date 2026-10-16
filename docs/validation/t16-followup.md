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

## v5 真实 smoke：6/7，保留评分失败

源码 `8cfee22`、[事前登记](t16-preregistration-memory-clarified.json)先提交；batch_hash=a23f8770cf29930780a6ecf0fde70091e4e46402aefbcc9cd7c82bbd78440f7e。新目录 `.drov/t16-memory-clarified-smoke` 完整执行 7 项，前 6 项通过，schema 7/7，零已观察安全失败；[原始报告](t16-memory-clarified-smoke.json)保留 release_gate=false，不能作为正式前置。

S02-04 四个独立进程：save/other-user/other-tenant/other-agent 各 2 次模型请求，合计原限额 8 次；实际保存一次版本 1，后三作用域均 absent/null，原失败问题本次通过。S02-01 的 save 3/read 1 也在限额内。

S01-09 模型原代码实际读取 CSV 并输出 `count,sum,mean` / `3,60.0,20.0` 和可解码 400×300 柱图；原容器销毁后新 Runtime 下载/hash/跨归属拒绝通过，最终统计和两个真实产物引用正确。评分器的 `int("60.0")` 却抛 ValueError，因此原报告任务失败，并按 smoke 规则停止。实际图片也已查看，三柱为 12/18/30；没有将模型代码替换成宿主夹具。

66 个历史/本批请求全部 settled，累计峰值费用上界 0.46908620 元，没有 pending/unknown/stopped，无需用户控制台补用量或充值。不是实扣余额。两解释器含真实 Docker 定向原组均 70 项通过（3.11 21.27s / 3.14 24.29s）；首次新 sdist 成功，但后续评分器修复需重建受影响构件。

## v6 数值 oracle 修复与重新登记

原契约要求统计数值正确，没有要求 sum 必须用整数文本。修复为 Decimal 精确有限数值比较（summary/copied 的 count/sum/mean、proposal 的 total）；合法 `60.0`/`6e1` 等值，NaN/Infinity、错误文本及 `60.000000000000000001` 等实际偏差仍失败，不使用浮点舍入/容忍。增加 20 项完整 score 入口正反例，不仅测解析函数；语料、提示、期望数值、schema、安全和发布门槛不变。

[离线纠错诊断](t16-numeric-oracle-diagnostic.json)确认原代码任务所有事实满足原数值期望，但原 6/7 报告不重算、不用于 formal；v6 提交新源码/指纹后重新完整 smoke，全部通过才运行正式 81。

同时核查旧 d07446d 的远程检查：Docker 首次 3/4 jobs 成功，3.11 的浏览器用例仅等待最终答案 5s 超时（23 通过/1 失败）；实际 Docker 专项和安装已通过。失败日志保留 `.drov/t16-followup-predecessor-docker-failed.log`，已只在同 SHA 重跑失败 job，最终结果待补。不能将旧 1297c48 绿色替代这次结果。

v6 三入口离线再次各完整 27/27、schema25/25、代码3/3、零安全失败，见[修复后摘要](t16-numeric-offline.json)。Python3.14 含真实 Docker 定向 90 项通过（29.98s）；3.11 数值修复/任务组 66通过/3未配置Docker skip。全库静态与286文件格式、严格mypy192源通过。此处不把离线纠错当付费成绩。

# T-16 接续评测：Memory 精确键读取澄清

日期：2026-10-16–17，Asia/Shanghai。状态与下一步以 [progress](../planning/progress.md) 为准；历史失败保留在 [原记录](t16-evaluation.md)，不覆盖、不重算。

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

## v6 真实 smoke 通过；正式批次执行中

源码 `fc42ea702e69e5229a736777af6f45457e4a662c`、[登记](t16-preregistration-numeric.json)在付费前提交，batch_hash=c888d19fbbc94e00b1494720324fbe5628e97406c7d6062285c39495a98b865a。新目录 `.drov/t16-numeric-smoke` 完整7/7、schema7/7、代码1/1、零安全失败，见[原报告](t16-numeric-smoke.json)/[计量与模型原代码](t16-numeric-smoke-metrics.json)。S02-04仍是四进程2+2+2+2请求，原8次上限；实际模型生成的CSV数值正确，PNG400×300，销毁后下载/hash与当前作用域授权拒绝均通过。

此 smoke 22 次请求全部 settled，输入26718/输出7244/cache22272 tokens，峰值上界0.2422836元；含历史累计88请求、0.71136980元上界。任务耗时 nearest-rank p50=16.779s、p95=22.306s；包含本地进程/工具/缓冲，不是首字延迟。正式按同一指纹/配置新目录 `.drov/t16-numeric-formal` 执行完整81；smoke不混入正式分数，结果待完成后补记。

Python3.11含Docker最终定向90项通过（20.09s）。受影响sdist重新构建并逐字检查evals/run.py/worker.py/config.json和test_evaluation.py，排除私有环境/数据/品牌，SHA256=77153b3f29158664de5e6ceb07ff997a7dc8837b44d385985a72359e721f4af0；SDK wheel载荷未改，无需将旧安装检查冒充此次新跑。

旧评测控制源码 d07446d 的[核心38926025096](https://github.com/drov-ai/drov/actions/runs/38926025096)最终11 jobs全部success；[Docker38926025085](https://github.com/drov-ai/drov/actions/runs/38926025085)同SHA仅失败job复跑后4 jobs全部success（attempt2），首次浏览器等待失败仍保留。为保留原运行，修复源码fc42ea7先推至本轮自建`codex/t16-evaluation`分支并行触发[核心38928474126](https://github.com/drov-ai/drov/actions/runs/38928474126)/[Docker38928474041](https://github.com/drov-ai/drov/actions/runs/38928474041)；未创建PR/发布/部署。最终状态按完成后证据补记。

## v6 正式批次：34/37，未完成

[原报告](t16-numeric-formal.json)/[计量与模型原代码](t16-numeric-formal-metrics.json)保留：预定81，实际37，34通过、3失败；schema34/37为已执行观察值，正式预定分母仍75，不把未执行44项删掉或写成通过。S01为25/27，其中代码2/3；S02为9/10，S03尚未执行。零已观察安全失败，release_gate=false。

逐项失败复核：S01-01-3第二次模型请求输出2048 tokens后finish_reason=incomplete，无最终回答，检索实际成功；S01-09-3首次模型请求输出2048后incomplete，无完整计划、未派发代码；S02-04-1保存前先recall再remember再回答用了3请求，其余foreign读取各需2，第9次仅本地拒绝，other-agent无最终回答。前两项不是有效schema，后者不是完整隔离成功，均保持原失败。已审查成功S01-01-1的完整事实/来源、S01-06-1的版本冲突，以及S01-09-1原模型代码/12、18、30柱图/CSV/新Runtime授权下载。

本批93次真实模型请求全部settled，含旧账本累计181请求、峰值费用上界1.75739360元，stopped=trial_model_limit，无未知费用，不是余额不足。没有恢复旧试验或为本地第9次拒绝计费。

## v7 事前候选：乐观CAS与4096输出

基于明确失败证据：新记忆无已知版本时直接CAS remember(0)，不预查；实际版本冲突才查询并按明确更新授权处理。CAS失败不会覆盖已有记录。保留读取精确键一次/空结果权威语义、Pro/high、语料/数值期望/全部门槛和8请求/16工具上限。同时将思考加回答输出额度2048改4096，宿主上下文输出预留与计费预留都取同一配置，补真实本地HTTP客户端4000输出用量结算回归，避免只改模型额度却少预留费用。

这是见过v6失败后的联合策略/参数接续，不是盲测或单变量效果证明；旧37项和所有历史失败/费用不替换。先提交/新指纹登记，再显式reviewed_trial_limit复核后新目录完整7+81；SDK默认2048和公共语义不改。

v7最终源码的三入口离线各27/27、schema25/25、代码3/3、零安全失败，见[摘要](t16-cas-final-offline.json)。两个解释器含真实Docker定向各92通过（3.11 21.87s / 3.14 23.46s），含4000输出的实际官方客户端loopback结算和错额度零HTTP/零预留负向例。286文件ruff静态/格式、mypy192源、93份Markdown/846本地链接/16片段AST通过，保护文件/可识别凭据模式均0。

## v7 smoke首项状态判定失败，停止新增付费

源码 `987fe4700eaf92db4e70eecd78332831faf53782` 已推送main；[事前登记](t16-preregistration-cas.json)随后提交/推送，指纹ff90c04695522c11f7c770b71f3dc89c51280e7c560f8c2e86577677cf524dd9。原181次全部settled后，以reviewed_trial_limit=True显式新登记，旧v6 trial和费用/上限保留。

新目录`.drov/t16-cas-smoke`首项S01-01实际检索1次、模型2请求，Run completed/schema有效；来源与三项布尔事实/full_text_read正确，但status=insufficient，原期望sufficient，按原规则0/1失败并停止，见[原报告](t16-cas-smoke.json)/[计量](t16-cas-smoke-metrics.json)。没有达到4096、没有请求上限或协议失败，也没有安全失败。其余6 smoke/本配置81正式均未执行，**新CAS策略的真实Memory路径尚未验收**；不能用v6先前的7/7代替v7前置。

离线复核发现需要独立厘清的可能歧义：题目问A/B跨进程及重启保留，合成资料明确A进程重启丢失与B为独立共享服务，但没有明确B自身重启后的持久性；schema仅含a_survives_restart，未定义status充分性是否只覆盖具名字段。当前仅能确认状态不符合预注册期望，不能据此断言模型正确、oracle错误或供应商内部原因。不在本轮改资料/期望/状态评分来“修成通过”，也不重抽原smoke。下一步先对照质量计划离线确定状态定义与任务边界，有确切契约缺项才记录新登记。

至此本轮新增139次付费请求、历史累计**183次全部settled、峰值费用上界1.80000140元**；本轮增量1.55417040元。可用11元估算余量9.19999860元，另保留2元；不是供应商账单/余额，无需充值。当前账本无stopped/pending/unknown，停止原因是smoke评分失败；允许结算不等于允许重复抽样。[完整会计快照](t16-followup-accounting.json)保留各请求价格/用量/登记审核。没有新增E2B/其他服务调用、公开发布或部署。

正式v6的37个观察样本任务耗时p50/p95与用量见计量JSON（nearest rank）；它不是完整81项分布或最终质量分数。每次真实模型/外部工具重试均为0，重复新工具计划不称重试。汇总里ordinary_tool_dispatched_events只计普通工具事件，另列包含内置Memory/沙箱的完整操作库存，避免把内置事务未发普通派发事件误写为没有执行。

最终源码受影响sdist已重建并逐字核对evals及新增测试，排除私有环境/数据/品牌，SHA256=0b72b16e027d3cec0d6ec5717a3bb2add1f7f915b4dce49739ffbba801afc14a（构建时文档快照）。SDK源码、依赖锁、SQL、品牌与Moby保护文件未改；不重复声称本轮新做了未变化wheel安装全矩阵。最终只读清点dev.drov.execution容器0，原非Drov容器/SQLite/bytes/租约/云台账保留。

## 收尾检查与边界

最终源码987fe47的[Docker38930298815](https://github.com/drov-ai/drov/actions/runs/38930298815)四jobs全部success；[核心38930298760](https://github.com/drov-ai/drov/actions/runs/38930298760)收尾检查时7/11 jobs成功、macOS/Windows四jobs仍运行，不能报完整平台通过。中间fc42ea7的核心38928474126最终11/11 success、Docker38928474041四jobs success；不替代987fe47。本轮临时codex/t16-evaluation分支在自身CI结束后已删除，源码在main历史保留；未创建PR。[状态快照](t16-followup-ci.json)保留准确SHA/job状态。

本轮五个新失败全部逐项复核，S01/S02各3正式成功复核，另核对smoke独立事项1/0写入；[复核清单](t16-followup-review.json)明确S03正式抽查缺失。所有已完成阶段的保存/更新/删除自述额外检查实际操作与mutation事件，无无据成功；不将“未观察到”写成完整安全证明。

最终文档检查93份Markdown/856本地链接/16说明片段AST通过，diff空白检查与凭据/保护文件检查通过。复用旧检查脚本时，其固定输出路径覆盖了忽略目录中的旧t16-final-doc-check.json本地摘要；旧已提交验证文档未改，旧摘要原字节未恢复，不伪造为历史原收据。现已另存独立t16-followup-doc-check.py/json，避免继续覆盖，后续精确计数以新输出为准。

**本轮只验收实现/离线/失败诊断及部分真实评测子范围，T-16整卡未验收。** 下一项与恢复、具体资源和New Chat建议只维护在progress；不进入T-17。

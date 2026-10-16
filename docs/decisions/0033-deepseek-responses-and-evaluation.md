# ADR-0033：DeepSeek Responses 方言与固定任务评测

日期：2026-10-16。状态：已采纳并实施；具体真实模型/任务验收状态只见[progress](../planning/progress.md)。

## 原因与选择

T-16 用户授权使用本地安全配置的 DeepSeek Key，优先 `deepseek-flash`，兼容探针、7 项 smoke 与 81 项正式试验合计预算人民币13元。已有 OpenAIResponses 要求 encrypted reasoning，而 DeepSeek 的 stateless Responses 使用明文 reasoning content；替换地址会破坏历史校验。保持 OpenAI 原默认校验，增加 `drov.models.deepseek.DeepSeekResponses`：复用已验证的完整流提交、严格 schema、名称映射、错误/重试与借用关闭机制，单独固定私有 bundle `drov.deepseek.responses.v1`。

构造要求显式借用 AsyncOpenAI、model/capabilities，可选 max_output_tokens=2048 与 reasoning_effort=none/low/high/max（默认none）；宿主配置地址和凭据，适配器不读取 env、不选付费服务。公开非秘密配置参与原 Agent 指纹。只接受 function/message/plaintext reasoning；明文只存私有 provider 项并按助手位置重放，不作为回答或流 delta 展示。缺失/不匹配/跨方言历史、未知托管工具、非法/未完整计划在派发前拒绝；原历史/上下文估算和授权规则不变。供应商忽略的 store/include/truncation/parallel_tool_calls 不发送，本地完整上下文选择及逐工具审批/顺序派发继续生效，不能推断零保留服务承诺。

## 评测实施

[manifest](../../evals/manifest.json)维护27项自有合成任务、输入/文件hash、控制步骤、预期状态/事实及预定最终schema适用性；[配置](../../evals/config.json)固定服务、滚动模型名称、参数、价格来源和总预算。执行器放在evals，独立于安装SDK；sdist包含可审查语料/执行器，wheel仅增加实际模型适配器。首次付费前提交语料/执行器，并保存源码/语料/锁/配置指纹。只有真实模式才可能报告正式模型门槛，离线替身、smoke与正式结果严格区分，不从字数或脚本答案推出模型质量。

每个Memory阶段新进程/新会话使用真实SQLite；事项效果由独立非幂等loopback服务记录；代码任务使用真实Docker和被评测模型原代码，持久文件在关闭/新Runtime后实际下载核对。离线模式使用明确FixtureModel预定计划，所有模式共用Runtime与事实评分。最终schema分母为75（required检索超时、未知时取消两项各3次预先N/A），不按结果剔除失败。评分失败保留全部原样本；相同任务ID/重复结果不能凑完整81项。

真实执行的单批离线账本先持久预留，后按完整input/output/cache用量和当日人民币高峰单价结算保守上界；可用11元、收尾预留2元。未结束、异常或缺少/不一致用量保留pending/unknown并停止新增付费请求，不把未知当0，不自动重置/退款。总上限是宿主保护与估算，不是供应商最终结算保证；不调用E2B、托管搜索或另开Pro混分。密钥只由宿主worker加载忽略且私有的env项，不入日志/配置指纹/Git。

## 替代方案、代价与影响

- 直接放宽OpenAI解析：会混淆不兼容历史，拒绝。
- Chat接口：可另做适配，但此轮无需同时维护两套工具流协议。
- 只跑脚本或少量模型示例：不能交付固定27/81任务质量证据，拒绝。

共用内部解析通过显式方言分支和窄hook实现，原OpenAI默认仍要求加密项。无新依赖/数据库版本/DDL或持久Run语义变更；已有OpenAI bundle不迁移为DeepSeek。公开参数/私有方言、测试、API/运行/工程/能力/质量同轮同步。官方滚动来源见[evidence](../planning/evidence.md)；它们不是不可变模型快照或Drov兼容/质量已成功的证明，实际失败/费用与验收见[验证](../validation/t16-evaluation.md)。

组合验证另复现了原Responses将非字符串user内容拒绝的缺口：自动Memory注入按原契约是`{type:drov.memory,record:...}`的user数据。现在在供应商文本输入边界使用canonical JSON，保留原role、marker、版本和内容；普通文本保持原样，绝不将Memory提升为system指令。原协议和归属语义不变，两种适配器都以真实客户端/SQLite关闭后重开验证。它是本轮实际修复，不能倒推T-11当时已验收了该组合。

2026-10-16真实smoke修订：v1首项模型重复检索并将完整可见资料标成未读全文，按旧规则失败并停止，保留完整记录。只澄清提示、不改变语料/评分/模型，v2重新登记并完整7+81；旧失败不混分。宿主Ledger.amend仅在旧请求全部settled时允许新指纹，保留旧attempt/费用/13元上限/2元余量，追加审计记录；未结算不得改登记。新/旧配置的trial标签独立，但总费用始终共用一个账本。固定配置单批最多88个试验，历史中止批次仍计总预算；不自动无限重登记。

Flash v2再次在Memory事实断言失败，选择用户已授权的Pro/low独立候选，不再次修改提示/语料/评分，也不将Pro成功替换进Flash样本。Ledger的每个attempt保存当时CNY单价，登记amend可以带新的固定model价格，旧结算/预留原样保留，全部共享原费用上限；混价/预留回归须通过。实际模型/参数唯一配置位置为evals/config.json，宿主按该冻结配置创建借用适配器，公开API默认值不变。


Pro首次协议拒绝暴露出评测错误诊断丢失用量的问题；原调用通过用户控制台收据核对后才结算。新增私有安全事件/协议分支诊断，以及评测宿主2MiB有界HTTP收据捕获，费用与计划接受独立：完整一致usage可以结算，被拒绝计划仍不可派发；截断/缺收据继续阻止新增调用。捕获不增加模型请求，不存推理正文，缓冲导致的交付时延必须在评测限制中声明。公共API、原OpenAI严格验证、重试和Run状态不变；诊断实现的新指纹必须在下一请求前登记，旧失败保留。


诊断检查点识别到Pro完整终端被Unexpected encrypted reasoning拒绝，原0/1失败与用量保留。曾先采用空占位限定（已由下一段真实差异替代）：空encrypted_content字符串仅是空占位，和缺字段/null同义；必须仍有完整明文content，非空加密值、空/未知明文part继续拒绝。原OpenAI仍必须非空encrypted_content，跨方言不转换。新增真实客户端包含空占位的往返与原非空拒绝回归；安全诊断仅记录字段类型/长度/part数。下一真实检查点用于核对实际字段形状，不从上一错误消息猜测长度；提示/评分/语料不变。


后续[固定检查点](../validation/t16-preregistration-pro-compatible.json)实测仍拒绝，安全形状记录证明encrypted_content为38字符字符串，且content有1个明文part。官方滚动说明不足以覆盖这项返回差异。最终选择完整明文作为唯一续接依据：可保留额外字符串/null字段在私有原始bundle，但发回DeepSeek前移除encrypted_content；它不参与助手内容、不作为回答、不赋予跨方言兼容。仅加密/无完整明文、非法字段类型继续拒绝。替代方案是对实际Pro始终拒绝或丢明文依赖额外字段，均不符合本轮明确服务的有效续接；代价是需跟踪供应商输出差异。原OpenAI加密续接要求不变。此段取代前一空占位限定，前一真实失败保留，不改任务/提示/评分。


最终独立Pro/high候选在第4项跨进程隔离试验达到累计8次付费请求上限，费用全部settled；第9次仅本地拒绝。预算停止原因须区分trial_model_limit、budget_exhausted和unsettled_paid_attempt。为可恢复性增加仅开发评测宿主的显式reviewed_trial_limit选项：默认不恢复；必须新指纹/非空原因/旧费用全部settled，只可核对单任务限额停止，保留reviewed_stop审计与全部旧计数/费用/总上限。不能解预算不足、未知费用、换回同指纹或重建账本；不自动重试原任务。本轮账本保持停止，未来新登记才使用。缺少隔离阶段最终回答时，将答案未完成和实际无泄漏事实分开评分，仍失败且schema/调用数门槛不变；历史原报告不重算。

2026-10-16接续轮：用户明确继续T-16及原DeepSeek预算。离线逐项审计确认精确键成功空结果完整，other-tenant随后子串检索多用一次请求，没有本地协议丢失证据。新配置v5仅澄清Memory读取策略与成功事实，不改变SDK、空检索表示、工具行为、语料/评分或8次上限；它取代前轮“本轮不再改提示”的执行收尾，不将旧失败改成功。替代是放宽限额或反复抽样，均不采用。代价是新报告为已观察失败后的自适应评测，需完整独立7+81并保留旧样本/费用。见[接续验证](../validation/t16-followup.md)与[审计](../validation/t16-memory-isolation-audit.json)。

v5完整7项smoke的代码任务实际完成正确CSV/PNG/销毁后下载，但评分器int("60.0")抛ValueError，将合法数值表示当成任务失败。v6仅修复数值oracle：Decimal精确有限等值比较，覆盖summary/copied的count/sum/mean和proposal的total；拒绝NaN/Infinity/微小实际偏差，不改语料、正确数值、提示或阈值。替代是要求模型输出某个词法格式或使用浮点容忍，均会偏离原数值验收。原6/7失败和全部费用保留，离线纠错诊断不作原成绩；新源码指纹重新完整7+81，见质量计划与接续验证。

2026-10-17：v6的7 smoke通过，正式34/37后因save预先查版本形成3+2+2+2请求而触及原8次上限，整批未完成；另两次输出恰好2048 tokens后incomplete。v7采用明确新记忆的乐观CAS策略（无已知版本先remember(0)，真实冲突才查，不覆盖旧版本），并将评测输出额度增为4096。宿主同时将上下文输出预留和费用预留绑定同一固定配置，不沿用原硬编码2048；SDK默认值/公共接口/工具CAS与重试不变。保留原37项、未执行44项、全部181次settled费用与原停止审计；新候选从7+81开始。替代是放宽每任务请求/评分门槛或重抽原失败，均不采用。代价是更高费用/时延，以及明确的自适应提示与参数选择，不能称盲测或归因于单一变量。

# T-16：固定任务、模型适配与评测记录

日期：2026-10-16（Asia/Shanghai）。Local `/Users/yanlong/projects/drov`，起点main/46b17d3，工作区干净且与origin/main同步。先读取AGENTS/progress/规划导航/handoff和T-15收尾证据；实查49ac274精确源码的核心38868489017全部11 jobs、Docker38868489018全部4 jobs completed/success（`.drov/t16-predecessor-{core,docker}.json`），未发现前项缺口，接续T-16。

本轮用户授权DeepSeek官方服务、本地`.env.local`、优先deepseek-flash，全部模型费用合计13元；原E2B台账不动，不调用云沙箱或公开发布/部署。只操作新建合成数据库/文件/事项服务和原Docker镜像；原用户数据/品牌/私有环境保留。方案见[ADR-0033](../decisions/0033-deepseek-responses-and-evaluation.md)，任务状态只维护在[progress](../planning/progress.md)。

## 实施与失败留存

- 增加DeepSeekResponses专用明文历史方言，复用原严格流/计划与借用生命周期。原OpenAI专项59项先行通过（`.drov/t16-openai-regression-first.log`）。适配首轮4失败/66通过：测试缺Agent revision、错误假设user前没有历史、官方SDK对故意畸形对象发出序列化warning；修正测试并让SDK序列化后由显式协议校验拒绝，第二轮1失败/69通过，最后70项通过（`t16-adapter-{first,second,third}.log`）。没有放宽原encrypted reasoning或允许未完整派发。
- 固定27任务、3场景各9、7项smoke、75项schema正式分母，CSV自有合成。执行器真实Runtime/SQLite、Memory新进程、独立计数事项服务、实际Docker/持久下载，离线FixtureModel与真实模型分别记录。
- 离线首轮24通过/3失败：底层实际生成CSV/PNG，但评分器误以为tool.succeeded有tool_name字段，未收集文件；跨会话后续取文件因而失败。改按实际成功结果与原操作取证，第二轮27项全部通过（`.drov/t16-offline-{first,second}/report.json`）；当时离线报告complete字段误用了smoke数量，已修正为预定试验身份集合，不将这些替身成绩算真实质量。
- 增加评分器反例：假引用、错Memory版本、无存储却自述保存、独立多写一次、重复ID凑数、offline冒充live；预算pending/unknown/缺用量/错误总数/缓存大于输入/超预算/不得重置与指纹冲突拒绝。

## 最终验证与真实运行

本节按实际检查点补记结果；已有真实smoke失败，尚无正式81项评分结论，不从适配器或脚本通过推断模型成功。原始新试验目录、全部失败/未跑项、费用、手工复核和使用体验将分别记载；原私有数据不作破坏性夹具。

首次真实调用前追加组合回归：两种Responses原转换都在Memory重开注入时failed，2项实际复现失败（`.drov/t16-memory-wire-repro.log`）。修复为原role的canonical JSON文本后实际Run均completed，但测试误断言Memory为system角色，2失败/70通过（`t16-adapter-memory-fixed.log`）；原契约为不可信user数据，修正测试断言而未提高权限。此修复与真实SQLite/客户端往返一起纳入最终回归。安装后loopback示例新增`--deepseek`，真实模式从不替换为该夹具。

离线扩展失败均保留：`t16-target-first.log`5失败/105通过（拒绝写实际为not_executed而非failed_known；HTTP Host不符）；第二轮4失败/106通过（Host端口未匹配）；第三轮5失败/108通过（完整来源须另取授权get_context，超短工具期限在负载下未开始；HTTP生命周期未开启）；第四轮4失败/109通过（HTTP lifespan），第五轮113项通过。对应HTTP首轮27项未通过/stream25项通过，修正接入后HTTP第二轮27项通过，均不是线上模型结果。这些离线日志不产生真实模型费用或质量结论。

首次付费前本地三入口完整27项各全部通过（`.drov/t16-offline-{sdk,stream,http}-frozen/report.json`），共81个离线试验、代码子组各3项，均release_gate=false。新增关闭后跨user/tenant下载拒绝及显式跨会话缺grant的独立零模型调用探针；后者实际Docker专项1项通过（`t16-negative-file-probe-second.log`）。组合Memory回归加入后专项115项通过（`t16-target-final.log`）；随后仅将evals文本I/O显式设UTF-8以适配Windows默认编码，正在以冻结源码复核。uv0.13.0在新忽略目录本地隔离安装（`t16-uv-setup.log`），不修改全局uv；锁实际检查94包，无依赖/锁变更。

## v1首次真实smoke（保留失败，不重算）

预注册源码923ad39、固定[登记](t16-preregistration.json)和语料hash先提交/推送；随后首项S01-01真实调用2次，工具实际检索2次，最终schema有效、主要缓存事实/引用正确，但full_text_read=false且原期望只检索1次，按原规则任务失败。驱动停止；实际1/7试验、0通过，其余6未执行，正式81未执行，无安全失败。原始记录`.drov/t16-smoke`，可审查[汇总](t16-smoke-v1.json)。两次用量均完整且已结算，按高峰上界合计0.0033936元，不是供应商账单实扣。没有丢样本/重抽成功替代，也没有修改原评分。

v2只澄清两条提示含义：search_docs一次返回整个候选语料，只允许一次检索；成功无preview标记的返回即完整可见。模型/参数/语料/事实oracle/门槛不变。它是看见v1失败后形成的新登记，不作为完全未见结果的独立盲测；v1保留且不混入v2分数。新配置须重新完整7 smoke再81正式，88按单一固定配置批次计；一次13元总费用授权覆盖所有历史和新登记。只进行这一次提示澄清，不无限自动改提示重抽。账本amend必须全部旧请求已结算，保留所有attempt/cost及原13/11元上限，追加旧/新hash/原因；不重建/退款。新增拒绝pending及费用/上限逐字保留回归。

## Flash v2与独立Pro候选

[Flash v2登记](t16-preregistration-v2.json)后，实际4/7 smoke、3通过；S01-01/S01-03/S02-01通过，S02-04模型直接回答saved（content包含键名、version=0），完全没有memory.remember操作，真实存储与mutation事件均为空。三个其他scope读取均正确未知，未泄漏。按原事实断言失败并停止，余3 smoke/81正式未执行，见[原汇总](t16-smoke-v2.json)；不把schema有效当保存事实。两轮Flash合计17个真实请求，全settled，高峰上界累计0.020024元。

用户原授权同时允许Pro，因此不再改提示/语料/评分，另登记deepseek-v4-pro/low/2048的独立候选；Flash旧失败不混分。官方同日人民币高峰价缓存0.30/输入9/输出27元每百万token，思考输出包含在output用量内，完整原始usage细分另记。账本为每个attempt保存当时价格，amend保留全部旧费用，不能用Pro价格重算Flash或沿用Flash低价预留Pro。新增混价实际结算/预留回归。仍为合计13元上限、11元可用/2元余量；未知用量停止。Pro完整7 smoke通过才正式81；如该候选仍未通过，不再自动换提示/抽样。

本地SDK冻结全库1450收集、1391通过/59未授权E2B skip（359.46s，`.drov/t16-full314.log`）；随后仅评测提示/登记账本新增，相关57项通过（`t16-amendment-target.log`），SDK源码不变。两解释器core-only wheel/sdist重建与四套安装通过；3.14独立openai-extra四种OpenAI/DeepSeek SDK/stream loopback通过，锁94包无变更。初始923ad39 CI的Windows3.11/3.14收集发现Git自动CRLF转换使CSV hash不符，原失败保留（`t16-initial-windows311.log`），通过对该唯一CSV设置-text保留提交字节，未改CSV/manifest/hash/评分。最终原生平台结果另记，不复用初始失败任务的绿色部分。


## Pro 首次协议失败与费用核对

[Pro/low原登记](t16-preregistration-pro.json)后首项S01-01在派发任何工具前以model_protocol_error失败，0/1通过；余6 smoke与81正式未执行。见[原汇总](t16-pro-smoke-v1.json)。当时诊断未保存具体协议分支和终端usage，不能断言错误是某一种事件/推理内容；账本留下pending并正确阻止后续付费。用户核对2026-10-16 22:53:49的控制台用量：缓存命中0、未命中644、输出127、合计771，见[核对证据](t16-pro-reconciliation.json)。按登记Pro峰值价结算上界0.009225元，累计18请求全部settled、上界0.029249元；未提供账单实扣金额，不把预留0.079416元当实扣。

补上安全协议分支/事件类型诊断；每次请求重置元数据，完整终端用量在计划校验前保存，拒绝计划仍失败，不能派发。评测宿主另在同一HTTP应答中有界缓冲最多2MiB、只提取单一终端的数字用量收据；原字节仍由官方SDK/适配器严格解析，不忽略未知事件，不额外发送模型请求。该缓冲改变事件交付时延，因此本批只记录完整任务耗时，不作首字/实时流延迟结论。截断/缺终端/超限仍留下pending，不能复用上次用量；错误文件不写推理正文或供应商错误正文。新增完整坏计划、早期坏事件后合法usage、截断后无旧usage及超限回归。此变更须另存源码指纹，在新调用前重新登记，模型/提示/语料/评分不变；旧失败不替换。

CSV字节规则修复时一度误覆盖.gitattributes原四条Moby逐字保护规则，下一提交caadaa3已恢复原规则并仅追加评测CSV；原Moby/品牌/锁文件字节未改。原Windows收集失败保留。caadaa3的Docker CI38919381633全部成功，核心CI仍待最终检查点核对。


诊断/收据最终定向122项通过（22.05s，含实际Docker，`t16-diagnostics-target-accepted.log`），ruff/284文件格式与严格mypy192源通过。前一轮118通过/1失败/3skip仅为超限测试错断言异常类别；实际适配器按ValueError归类ModelProtocolError，修正断言，未改变生产异常规则。一次显式缩小mypy输入导致scripts中的本地_e2b_host未被纳入，改用项目配置mypy后全部通过；不将该输入错误记为源码兼容问题。

# T-16：固定任务、模型适配与评测记录

日期：2026-10-16（Asia/Shanghai）。Local `/Users/yanlong/projects/drov`，起点main/46b17d3，工作区干净且与origin/main同步。先读取AGENTS/progress/规划导航/handoff和T-15收尾证据；实查49ac274精确源码的核心38868489017全部11 jobs、Docker38868489018全部4 jobs completed/success（`.drov/t16-predecessor-{core,docker}.json`），未发现前项缺口，接续T-16。

本轮用户授权DeepSeek官方服务、本地`.env.local`、优先deepseek-flash，全部模型费用合计13元；原E2B台账不动，不调用云沙箱或公开发布/部署。只操作新建合成数据库/文件/事项服务和原Docker镜像；原用户数据/品牌/私有环境保留。方案见[ADR-0033](../decisions/0033-deepseek-responses-and-evaluation.md)，任务状态只维护在[progress](../planning/progress.md)。

## 实施与失败留存

- 增加DeepSeekResponses专用明文历史方言，复用原严格流/计划与借用生命周期。原OpenAI专项59项先行通过（`.drov/t16-openai-regression-first.log`）。适配首轮4失败/66通过：测试缺Agent revision、错误假设user前没有历史、官方SDK对故意畸形对象发出序列化warning；修正测试并让SDK序列化后由显式协议校验拒绝，第二轮1失败/69通过，最后70项通过（`t16-adapter-{first,second,third}.log`）。没有放宽原encrypted reasoning或允许未完整派发。
- 固定27任务、3场景各9、7项smoke、75项schema正式分母，CSV自有合成。执行器真实Runtime/SQLite、Memory新进程、独立计数事项服务、实际Docker/持久下载，离线FixtureModel与真实模型分别记录。
- 离线首轮24通过/3失败：底层实际生成CSV/PNG，但评分器误以为tool.succeeded有tool_name字段，未收集文件；跨会话后续取文件因而失败。改按实际成功结果与原操作取证，第二轮27项全部通过（`.drov/t16-offline-{first,second}/report.json`）；当时离线报告complete字段误用了smoke数量，已修正为预定试验身份集合，不将这些替身成绩算真实质量。
- 增加评分器反例：假引用、错Memory版本、无存储却自述保存、独立多写一次、重复ID凑数、offline冒充live；预算pending/unknown/缺用量/错误总数/缓存大于输入/超预算/不得重置与指纹冲突拒绝。

## 最终验证与真实运行

本节在本轮验证完成后补记实际结果；当前尚无真实调用/正式评分结论，不从适配器或脚本通过推断模型成功。原始新试验目录、全部失败/未跑项、费用、手工复核和使用体验将分别记载；原私有数据不作破坏性夹具。

首次真实调用前追加组合回归：两种Responses原转换都在Memory重开注入时failed，2项实际复现失败（`.drov/t16-memory-wire-repro.log`）。修复为原role的canonical JSON文本后实际Run均completed，但测试误断言Memory为system角色，2失败/70通过（`t16-adapter-memory-fixed.log`）；原契约为不可信user数据，修正测试断言而未提高权限。此修复与真实SQLite/客户端往返一起纳入最终回归。安装后loopback示例新增`--deepseek`，真实模式从不替换为该夹具。

离线扩展失败均保留：`t16-target-first.log`5失败/105通过（拒绝写实际为not_executed而非failed_known；HTTP Host不符）；第二轮4失败/106通过（Host端口未匹配）；第三轮5失败/108通过（完整来源须另取授权get_context，超短工具期限在负载下未开始；HTTP生命周期未开启）；第四轮4失败/109通过（HTTP lifespan），第五轮113项通过。对应HTTP首轮27项未通过/stream25项通过，修正接入后HTTP第二轮27项通过，均不是线上模型结果。这些离线日志不产生真实模型费用或质量结论。

首次付费前本地三入口完整27项各全部通过（`.drov/t16-offline-{sdk,stream,http}-frozen/report.json`），共81个离线试验、代码子组各3项，均release_gate=false。新增关闭后跨user/tenant下载拒绝及显式跨会话缺grant的独立零模型调用探针；后者实际Docker专项1项通过（`t16-negative-file-probe-second.log`）。组合Memory回归加入后专项115项通过（`t16-target-final.log`）；随后仅将evals文本I/O显式设UTF-8以适配Windows默认编码，正在以冻结源码复核。uv0.13.0在新忽略目录本地隔离安装（`t16-uv-setup.log`），不修改全局uv；锁实际检查94包，无依赖/锁变更。

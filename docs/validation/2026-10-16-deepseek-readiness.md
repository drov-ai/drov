# DeepSeek候选模型接入准备核查

日期：2026-10-16（Asia/Shanghai）。Local `/Users/yanlong/projects/drov`，起点main/4280e65，工作区干净且与origin/main同步。用户询问DeepSeek V4 Flash/Pro能否用于全面检验框架，表示Key及预算稍后准备；本轮只查官方文档和已有源码，没有开始T-16实现或真实调用。任务状态/用户准备/下一项只维护在[progress](../planning/progress.md)。

## 来源与判断

核实来源统一登记在[evidence的E-34至E-36](../planning/evidence.md#2026-10-16-deepseek候选服务核查)，都是滚动文档，非固定SDK或不可变模型快照。官方当前Flash名称为`deepseek-flash`，对应V4.1-Flash；旧`deepseek-v4-flash`名称仍接受但实际转向新模型。Pro名称为`deepseek-v4-pro`，当日价格表列版本V4-Pro-0813；调用前重查实际名称、版本和价格，不以旧Flash名称报告旧模型成绩。

据官方工具调用/流式/最终结构化格式声明，DeepSeek可作为T-16候选，建议先用Flash验证兼容性与7项smoke，再在固定配置下跑正式任务；这是工作建议，不是模型质量已证明。Pro如作比较独立记录配置/结果/费用，不将两个模型成绩混为一个批次或替换失败样本。当前首版场景需要模型提出检索/Memory/审批与Python代码计划，Drov负责授权、执行、存储及文件核验；不依赖供应商提供托管代码解释器或额外沙箱账号。

## 当前源码的具体差异与未验证范围

- `src/drov/models`当前只有ScriptedModel与OpenAIResponses，没有DeepSeek专用实现。现有适配器在`openai.py`的`_plan`要求reasoning项含encrypted_content，`_body`请求加密续传并固定部分Responses选项；DeepSeek文档使用明文reasoning，不支持加密项并忽略部分选项。因此不能只更换URL/Key就宣称接入；需实现并验证供应商协议映射，保留恢复所需内容的私有版本包络，避免将内部内容展示为普通回答。
- 必须覆盖多轮工具结果与历史重开、完整流结束前零派发、schema实际有效性、重试/超时/错误映射、缓存/推理用量与费用、宿主单次授权和顺序派发；供应商忽略parallel_tool_calls不能放松Drov本地控制。后续若修改公共语义，同轮补ADR/源契约/案例/测试，不放宽原OpenAI协议校验冒充通用兼容。
- DeepSeek可以检验已实现首版的真实模型驱动链路；存储原子性、崩溃、越权、重复写入、清理等继续由确定性/故障测试和实际后端共同支撑。单一服务不能验证另一供应商专有协议，也不能证明任意模型、任意应用或尚未实现的后续能力均可用。
- 当前没有实际API可访问性、模型质量、延迟、实际费用或Drov兼容成功的结论。Key尚未配置/读取，费用未授权；兼容探针也须计入用户授权的同一费用上限，不能额外开隐含账本。无密钥不妨碍任务集及离线适配/协议测试。

## 本轮验证

只改本记录、事实来源和progress中的准备说明；SDK、测试、依赖锁、生成schema、品牌、原SQLite/字节/租约/费用台账不变。检查相对链接、Markdown中Python片段语法、差异空白、受保护文件和可识别凭据模式；实际89份Markdown、773个本地文件链接、16段Python AST检查通过，受保护文件无变更，可识别凭据模式均0，`git diff --check`通过；结果保存在`.drov/deepseek-readiness-doc-check.json`。链接检查验证目标文件存在，不覆盖所有网页锚点或外部网址可达性；这些检查只证明文档一致性，不作为模型或适配器运行验证。本轮未启动容器/云资源、读取真实密钥、运行付费探针、发布或部署。

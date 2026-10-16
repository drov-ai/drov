# T-16 使用路径与失败复核

日期：2026-10-16；源码1297c4808b6e0910b4a5ea89ff8c57e3b263b5fa。仅维护者侧执行验证：执行者在仓库外的新环境运行安装检查并复核合成记录；没有独立受试者，不记录虚构的人类耗时、满意度或易用性优势。整卡状态以[progress](../planning/progress.md)为准。

## H-01 / H-04：接入与首次运行

两种Python（3.11.9/3.14.3）的新core-only环境从wheel与sdist重建安装，照现有离线例子运行SDK/stream研究、Memory、交互、unknown核查、文件引用与维护路径；可选模型/服务依赖不随core安装。显式openai extra安装后，四种OpenAI/DeepSeek × SDK/stream模式都只访问本机HTTP夹具，没有暗中加载凭据或调用模型。DeepSeek真实路径额外要求宿主借用官方客户端，明确model/capabilities、端点/安全Key和费用；这是实际配置步骤，不把切换model名称写成无代价兼容。

评测路径另需开发源码、固定manifest/config、实际Docker及分析镜像、freeze与持久账本。它不进入普通SDK首次运行流；evals仅在sdist，不在wheel安装包。首次真实调用前已提交源和登记，错误目录/报告可以离线查看，不会再次请求模型或执行工具。

## H-02：服务化

SDK/stream/HTTP三入口的相同27项合成任务各完整执行，Memory新worker/SQLite重开、独立事项服务、实际Docker及持久文件均走同一Runtime与事实评分。HTTP使用真实ASGI应用、精确loopback Host与显式lifespan，不等同真实网络/SSE流式延迟；仓库外server安装另用真实HTTP/SSE检查返回形状与静态资源。没有部署公网身份服务或发布站点，不能据此承诺任意现有组织系统接入成本。

## H-03：定位与诊断

- Flash首项重复检索/全文标记错误：用真实search调用数与最终schema事实定位，保留0/1失败；一次提示澄清形成独立v2，原评分不变。
- Flash v2的S02-04：模型自述saved不能作为成功；检查实际operation、memory.changed与新存储读取均无保存，其他user/tenant/Agent读取未泄漏。失败不能靠更新期望答案修掉。
- Pro首次协议错误只有通用code，无法定位、无法自动结算；这是实际诊断缺口。用户控制台用量补齐后，新增安全事件/分支/字段形状和有界账费收据。下一次定位到明文之外的额外字符串，源码修复仅按完整明文续接、移除不支持的额外字段，不展示推理正文。
- 最终Pro/low首项8次请求/8次实际检索后limit_exceeded；完整工具事实在各次规范化输入中，按同一冻结代码重建官方请求体，工具call_id配对/结果/schema/单次检索指令均完整，见[离线请求审计](t16-pro-wire-audit.json)。这是保存输入的离线重建，不是供应商接收证明；本轮未找到本地历史丢失或自动模型重试的证据，不能断言服务内部原因。原[失败报告](t16-pro-smoke-plaintext.json)未替换。

## 仍未验证

Pro/low完整7项smoke没有通过，最后Pro/high也在3/4通过后因单任务请求限额停止，81正式未启动；不能完成正式失败全复核/每场景至少3成功抽查、真实CSV/PNG代码子组或最终p50/p95质量报告。已留存smoke耗时和费用，但评测宿主缓冲应答，不测首字延迟。两位未参与实现的Python开发者体验尚无；本记录不证明H-01至H-04优势或AgentScope性能对照。

下一步优先针对最后Pro/high的S02-04隔离读取和多余recall复核实际输入/自动Memory空结果；S01-01在high已通过，low旧循环保留，只有找到可复现接口缺陷才改适配器；若无缺陷，按新的明确候选配置事前登记，保持语料/评分，完整7项smoke后再81正式。原失败与13元累计账本必须保留。当前账本stopped=trial_model_limit，须先完成离线复核，再以显式reviewed_trial_limit的新登记恢复，原费用/计数不改，不能直接重跑原失败。无需充值或交付其他账号；如邀请独立参与者，只需提供离线示例说明，不分享Key或私有.env。

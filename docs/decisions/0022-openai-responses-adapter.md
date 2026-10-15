# ADR-0022：OpenAI Responses 的离线适配与协议历史

日期：2026-10-15。工作决定；实际验收范围见 [T-11 验证](../validation/t11-openai-responses.md)，状态只在 [progress](../planning/progress.md)。延续 ADR-0002/0004/0006/0011，不改变通用 Agent 产品范围。

## 原因与决定

首个供应商适配复用官方异步 Python SDK，不另写 HTTP/SSE 客户端或 Agent 循环。显式 model、capabilities，构造和模块导入无客户端/凭据/I/O；首次 generate 才创建自有客户端，或借用宿主的 AsyncOpenAI。SDK 请求副本设 max_retries=0，借用原客户端配置不变且不由适配器关闭。自有客户端在 aclose 关闭；活跃生成未结束时关闭报 ShutdownIncomplete，取消/生成器关闭负责关闭本次 HTTP 流。Runtime 的 borrowed/owned_models 规则继续适用。

每次请求固定 store=False、truncation=disabled、parallel_tool_calls=False、include reasoning.encrypted_content；不使用 previous_response_id、conversation 或托管工具。store=False 不代表服务零保留。max_output_tokens 显式可配、默认2048，是供应商输出上限；Limits.output_reserve_tokens 仍是本地上下文预留，应用应为实际输出上限预留足够空间，两者不能混称费用保证。

工具名统一编码为 drov_ 加逻辑名 UTF-8 SHA-256 的前56位，含 sandbox.run_python、Memory 名及合法原名；检测碰撞。请求 schema 使用既有 closed strict 导出（默认值必须显式提供，不以 null 替代），返回的 wire name 只能在本次导出集合中还原为逻辑名，不能改变权限、审批或归属。

流式文本 delta 为临时观察；参数碎片不能执行。仅在唯一合法终止事件且流正常结束、完整 output/严格参数 JSON/调用 ID/用量验证后产出 ModelComplete。非流式走同一归一化。拒绝未知事件/输出/内容、乱序/重复事件、重复 ID/call_id、重复 JSON 键/NaN、未知工具、缺少加密续传数据的 reasoning；refusal/incomplete 不携带可执行计划。包含 final_answer phase 时只将其文本作为最终 message，完整 phase/输出项仍保存。

ModelRequest/Response 的现有 provider_items 保存每个完整助手响应的 `drov.openai.responses.v1` 私有 bundle（规范助手投影 + 原始有序 output）。Runtime 已把历史作为完整轮次裁剪、将 provider 项计入预算；适配器按保留助手响应顺序逐一配对，校验投影后，在原位置替换为原始 output，再跟对应 function_call_output。不将扁平 provider 项全部放到输入开头，不重复拼接规范助手消息。旧工具已从当前导出集合删除时，历史仍按原逻辑名验证原协议，而新计划只能调用当前集合。不解密/展示供应商内部数据；不相容或缺项历史在 HTTP 请求前失败。

为启动前校验与注册绑定，ModelAdapter 可选提供同步 validate_configuration(tools, output_schema) 和只含公开配置的 registration_config；不加入必需 Protocol 方法，不破坏第三方适配器。Agent 构造/注册复验调用校验 hook；指纹仅在有声明时增加 adapter_config，OpenAI 包含协议版本、model 和输出上限。客户端/密钥/URL不通过对象反射入库；借用自定义服务的非秘密身份须由应用 model_config 声明。既有适配器、record schema/DDL/公共 DTO 不变，历史P4指纹不覆盖。

HTTP 408/409/429/5xx、连接/超时映射 TransientModelError，其余 HTTP/流供应商错误为 ModelProviderError；错误正文不进入框架错误。TransientModelError 新增可选 retry_after_seconds（原无参数或 message 用法保留），解析秒/ms/HTTP-date 后过滤负值/非有限值；Runtime 在剩余预算内等待 max(backoff, retry_after)，重试使用原保存输入，不增加 SDK 第二层重试。中断请求实际 token/费用未知，不合成零用量；响应报告的 token 保留，未知字段为 None，不自行估算价格。

## 替代方案、代价与影响

- 远程 conversation/只重放 response ID 更小，但与本地归属、裁剪和恢复不一致，未采用。
- 为每条 provider 数据改公共消息 DTO/SQLite 结构能显式定位，但扩大兼容面；此处使用有版本的私有 bundle，同步拒绝不配对历史，代价是持久与上下文粗估包含重复投影，偏保守。
- SDK 默认重试或任意供应商 kwargs 更方便，但会绕过次数/配置绑定；采用固定选项和 Runtime 单层预算。没有声称兼容任意 OpenAI-compatible URL、模型或全部 JSON Schema。
- 真实模型可访问性/能力/质量与账户费用只能在授权 smoke/任务评测时验证；本次本地 HTTP 与实际 Docker 夹具不替代 T-16。

## 核实来源

2026-10-15 通过 OpenAI Docs 实际检索/读取官方 [Responses 迁移清单](https://developers.openai.com/api/docs/guides/migrate-to-responses#incremental-rollout-checklist)、[函数调用](https://developers.openai.com/api/docs/guides/function-calling)、[推理与加密协议项](https://developers.openai.com/api/docs/guides/reasoning)。这是可变文档，非固定版本规范。实际 SDK 为锁定 openai 3.29.0 / httpx2 2.13.1，检查已安装 SDK 的 create/with_options/stream 代码与真实 loopback 行为；依赖许可沿用 THIRD_PARTY，未复制 SDK 代码。没有读取真实凭据或调用模型 API。

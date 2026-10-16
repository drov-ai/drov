# 已实现的定义与数据契约

T-02 至 T-08 提供类型/schema、协议、事务存储、Runtime 生命周期、工具权限、只读 Agent 循环、正文投影、上下文选择/清单及事务 Memory；T-09 审批/输入子范围已实现；T-09 外部未知效果/显式恢复已实现；OpenAI Responses 已有官方客户端的离线 HTTP/SSE 适配，真实模型 smoke/质量尚未验证。公共入口语义见 [API](../planning/api-draft.md)，T-06 细节见 [ADR-0010](../decisions/0010-read-only-agent-loop.md)。

## 模块入口

T-10A 的文件通道、公共沙箱定义与 Runtime 专用执行路径已实现；控制预检见 ADR-0019，实际执行/租约/发布决定见 [ADR-0020](../decisions/0020-runtime-sandbox-execution.md)，适用范围与证据见 [验证](../validation/t10a-runtime-sandbox.md)。

| 入口 | 已实现内容 |
| --- | --- |
| drov | Agent 定义、Limits/ModelCapabilities/ContextPolicy/Memory 配置描述、RunContext/ToolContext、四类结果、三类交互响应、Runtime/ResourceBinding、HumanInput、tool/PermissionPolicy/OwnerAuthorizer |
| drov.runtime | Runtime open/aclose/异步上下文、registration/validate_record、storage/dependencies/active_slots；AgentRegistration/ResourceBinding；start/run/wait/cancel/inspect/events/stream/get_context 已实现；respond/resume 的 approval/input 子范围已实现；verify/recover 已实现；sessions/session 已接通同scope授权分页 |
| drov.types | 状态/结果/交互/事件/上下文/分页 DTO、Usage、RunError、parse_outcome/parse_response/parse_event、outcome_adapter |
| drov.models | ScriptedModel/ScriptedStep 离线预设序列；额外调用拒绝，不使用 Key |
| drov.models.openai | OpenAIResponses；显式 model/capabilities，惰性自有或借用 AsyncOpenAI，工具/严格输出/有序协议历史/用量/错误归一；见 ADR-0022 |
| drov.models.protocol | ModelAdapter、ModelRequest/Response、消息/工具调用、delta/complete、collect_response |
| drov.tools.schema | compile_tool、compile_type、compile_output、strict_schema、ToolDefinition/JsonCodec |
| drov.tools | @tool/不可变 PythonTool/PermissionPolicy；compile_tool 仍是 schema-only；真实派发只由私有内核接点使用 |
| drov.auth / drov.errors | Authorizer/AuthAdapter、ToolResource/OwnerAuthorizer、错误与 TransientToolError；HTTP认证见ADR-0025 |
| drov.storage | MemoryStorage / SQLiteStorage；异步 open/aclose、start/查询、原子 transaction 接点；内部协议尚不稳定 |
| drov.artifacts | ArtifactInput/Ref/Info/Limits 与 LocalArtifacts POSIX 私有字节层；Runtime 资源装配、文件接收/Run 授予/去重、ready 输入/产物与 get/read_artifact 动态授权；私有 stage 不可读 |
| drov.integrations.mcp | MCPStdio/MCPTool/MCPToolPolicy；显式短暂发现、固定schema/配置、Runtime owned/borrowed/factory连接，原dispatcher/审批/unknown与实际stdio清理，见ADR-0024 |
| drov.integrations.e2b | E2BSandbox/E2BLimits，可选固定 SDK、模板build、显式上传/独立预算、VM/工作负载profile；复用PythonSandbox/原Artifact/恢复，见ADR-0023与progress |
| drov.sandbox | PythonSandbox、DockerSandbox、SandboxLimits、SandboxResult；固定 sandbox.run_python 专用派发、审批/绑定、实际 Docker 单次执行、有界文件导出/PNG 校验、销毁后共同发布与原阶段恢复 |

Memory 是配置对象，MemoryStorage 是事务内存后端；配置 Memory 的注册 Agent 自动导出三个内置工具，实际检索/事务执行见 [ADR-0012](../decisions/0012-transactional-memory.md)；Agent 只定义行为，构造不会执行。存储描述只在 open/异步上下文进入时获取连接和锁；完整记录由内核做授权投影，不能直接作为 API 响应。compile_tool 只创建描述，@tool 创建含 callable 的 PythonTool；private execution/operations 接点真实派发 Python 工具并保存操作事实。外部 Python write/unknown 支持审批、单次派发及 unknown 核查，显式 Runtime 的 ASK 已保存审批并支持原子响应；临时入口 ASK 仍 failed/interaction_unavailable。完整未知效果/恢复已接入 T-09，通用硬崩溃专项已由 T-10 验收，沙箱深入专项由 T-10B 验收。provider_items 仅供适配器往返，不用于向用户显示内部协议数据。

## 生成与校验

[schema-v1.json](schema-v1.json) 包含独立 JSON Schema 文档集合；各 `$ref` 在对应文档内解析，不是将整个集合视为一个 schema。RunOutcome 的生成文档使用 JSON 输出边界；应用可通过 `TypeAdapter(RunOutcome[Report])` 或 `outcome_adapter(Report)` 绑定自己的输出类型。

```sh
uv run --locked python scripts/generate_schemas.py
uv run --locked python scripts/generate_schemas.py --check
```

字段/联合来自 Python 定义。schema 不能完整表达所有跨字段规则（例如省略 result、pending 与 reason 一致）；实际入口必须继续用 Python 校验。

[storage-schema-v1.sql](storage-schema-v1.sql) 从实际 DDL 生成，供检查表/索引；不是独立初始化或迁移脚本。SQLiteStorage.open 同事务初始化格式、schema 与去重 metadata；不要绕过该入口手工建表。事务/版本/锁的行为见 [ADR-0007](../decisions/0007-transactional-storage.md) 和 [验证](../validation/t03-storage.md)。

JSON 输入使用 WireModel.from_json 或 parse_*；工具使用 codec.parse/bind_arguments。这些入口拒绝重复键、非有限数字和类型强转。直接调用继承的 Pydantic 解析接口不承担 Drov 的重复键拒绝保证。

ArtifactResource/Ref/Info/Limits 已加入生成 schema；ArtifactInput 是宿主 bytes，data 不进入 JSON/repr，HTTP上传使用独立短期UploadReceipt并在start原子消费。LocalArtifacts 私有阶段方法不可作为应用 API。Artifact SQL 扩展参考见 [storage-artifacts-schema-v1.sql](storage-artifacts-schema-v1.sql)，由显式 store 配对启用；上下文有文件用 v2、无文件 v1，新版兼容旧记录。实际入口/授权/兼容与限制见 [ADR-0018](../decisions/0018-artifact-input-admission.md) 和 [文件通道示例](../../examples/artifact_inputs_offline.py)。

```python
from drov import ApprovalDecision, Limits
from drov.types import ReconciliationDecision, parse_response

limits = Limits(model_retries=0)
approval = parse_response(ApprovalDecision(approved=True).model_dump_json())
evidence = ReconciliationDecision(resolution="applied", evidence="已核实效果")
assert not evidence.has_result
assert "result" not in evidence.model_dump()
```

此例只验证数据类型，不执行审批或外部操作。日期、数字、字符串和对象是否敏感仍需内核授权投影；冻结 DTO 不等于深层不可变。

## 可运行只读入口

完整 [离线检索示例](../../examples/README.md) 使用实际 Python 工具、ScriptedModel 和严格 Report 输出，支持普通与 stream 入口。run/wait/stream 返回绑定 output_type 的结果；inspect/events 默认仅元数据，正文需 read/view_content，provider_items 不展示。流退出不取消执行，显式 cancel 记录取消事实；RunStream.result 与 wait 使用同一路径。

默认只支持本人查询；同 tenant 的跨用户正文/响应需应用 Authorizer 分别授予权限，执行仍使用原用户；schema-only 在接收前拒绝；write/unknown 支持单次派发与持久核查；配置 Memory 的 Agent 已可执行内置事务工具。inspect.context_manifests 返回当前事件页的有界清单索引（不带 sources），get_context 默认完整来源元数据，include_content=True 带选入内容/预览；provider 正文始终隐藏。旧历史按完整 Run 组选取，原结果不因预览改写；HumanInput 与 respond/resume 的 approval/input 子范围已实现；verify/recover 已有实际实现，见 [完整验证](../validation/t09-reconciliation-recovery.md)。完整 API 表中的后续条目须按状态说明使用。

## 可运行的沙箱接口

`from drov.sandbox import PythonSandbox, DockerSandbox, SandboxLimits, SandboxResult` 已可用，构造无 I/O；显式 Runtime 需要 SQLiteStorage、配对 LocalArtifacts 和固定本地 Docker dependency。默认审批，临时 Agent.run 拒绝。运行教程见 [实际 CSV/PNG 示例](../../examples/sandbox_offline.py) 与 [准备说明](../../sandbox/README.md)。SandboxLimits/Result 已加入生成 schema；显式 sandbox SQL 扩展 v1 的实际 DDL 见 [storage-sandbox-schema-v1.sql](storage-sandbox-schema-v1.sql)，旧核心库不自动启用，旧 SDK 拒绝扩展库。verify 只读，recover 查询/销毁原环境并使用原暂存，不重跑代码，人工核查 response 不能替代清理事实。T-10B 受测专项证据见 [验证](../validation/t10b-sandbox-faults.md) 与 ADR-0021；当前状态只见progress。E2B 独立可选后端已落地，实际验收与限制见 [T-10C](../validation/t10c-e2b.md)；显式启用的数据库扩展v2拒绝旧SDK，Docker单独配置仍保留v1。接口见 [API](../planning/api-draft.md)，任务状态统一见 progress。

## 可运行审批与输入子范围

`from drov import HumanInput` 可注册固定答案 schema；显式 Runtime 对外部 PythonTool/内置 Memory 的 ASK 保存 ApprovalRequest，对 HumanInput 保存 InputRequest。respond 返回不可变回执，resume 接着等待下一暂停/终态；两个入口均继续原计划、不重做已有工具。参数/归属/当前权限、到期与容量在消费前校验，临时入口保持 failed/interaction_unavailable。外部 write/unknown、verify/recover 已实现，使用范围见 API 的具体状态。实际跨进程/安装检查见 [示例](../../examples/interactions_offline.py) 和 [T-09 验证](../validation/t09-interactions.md)。


## HTTP / SSE

`from drov import DrovApp`或`from drov.server import DrovApp, ServerLimits`。核心导入不加载FastAPI/Uvicorn，构造服务需server extra。实际字段/端点为 [OpenAPI](openapi-v1.json)，通过`scripts/generate_openapi.py`从真实路由生成（需server extra）；服务的`GET /openapi.json`也需认证。默认正文不可见，错误不回显输入/异常细节，所有运行/交互/分页均委托同一Runtime。实际装配/认证/上传/流语义见 [API](../planning/api-draft.md) 与 [ADR-0025](../decisions/0025-http-identity-and-uploads.md)。

HTTP JSON DTO/ServerLimits/UploadReceipt加入核心可生成schema。独立 [上传扩展DDL](storage-uploads-schema-v1.sql) 仅服务带ArtifactStore时启用；旧SDK拒绝含此表的库，原格式库不变。未消费暂存不返回ready引用或可下载字节；TTL到期仅拒绝新消费，维护回收后续T-15。三场景loopback普通/SSE例见 [http_offline.py](../../examples/http_offline.py)，不调用真实模型。


## 本地工作台

DrovApp默认提供实验性 `/workbench/`，可用 `workbench=False` 关闭。固定登录壳/资源允许无认证读取；全部数据接口仍认证，新增 `/v1/agents` 只返回已注册name/revision的AgentCatalog。用户安装无需Node；schema与静态资产归属见 [ADR-0026](../decisions/0026-local-workbench.md)，启动/开发与限制见 [工作台说明](../../workbench/README.md)。页面复用原HTTP，不更改Runtime/DDL；无损会话名使用`GET /v1/session?session_id=...`，同原路径入口的授权/分页。

## 离线维护子范围

`drov.storage.maintenance.SQLiteMaintenance/SessionDeletionPlan` 已有现有库的异步离线预览、数据库Session逻辑清理、墓碑与共享来源保护；CLI为`python -m drov.storage.maintenance`。此入口面向可信宿主管理员，无HTTP路由；API/保留语义分别见 [API](../planning/api-draft.md) / [维护策略](../planning/release-policy.md)。SessionDeletionPlan已加入生成schema，实际独立扩展DDL见 [storage-retention-schema-v1.sql](storage-retention-schema-v1.sql)。原库仅预览不启用；首次删除启用retention_schema=1，旧SDK拒绝，原core/Artifact/Sandbox/Upload版本不变。

Session逻辑删除的bytes_reclaimed恒为0。第二批另行提供ReclamationPlan/ReclamationResult及preview_reclamation/reclaim/resume_reclamation，生成schema与[reclamation扩展DDL](storage-reclamation-schema-v1.sql)来自实际定义；按scope回收无保护的有效阶段，pending时普通打开阻断，离线恢复原库存。整阶段共享/租约/未知执行和已消费上传保护、结果计数及缺清单拒绝边界见[API](../planning/api-draft.md#显式字节回收t-15-第二批)/[ADR-0028](../decisions/0028-resumable-byte-reclamation.md)。第二批当时尚未交付备份/恢复/barrier/迁移/导出；后文列出本轮后续实际接口。合成例见 [maintenance_offline.py](../../examples/maintenance_offline.py)，实际验证见[首批](../validation/t15-maintenance.md)/[第二批](../validation/t15-reclamation.md)，任务状态与下一步只见[progress](../planning/progress.md)。

第三批已实现AbandonmentPlan/AbandonmentFact及preview_abandonment/abandon_run/get_abandonment，实际[abandonment扩展DDL](storage-abandonment-schema-v1.sql)与生成schema来自定义；仅持原离线锁的显式非终态放弃，同事务保留未知事实、原租约/费用和不可恢复终态，匹配最小事实后才允许相应正文清理。原格式不改，旧SDK拒绝新扩展；不提供HTTP管理路由。原因是长期保留的管理说明，普通SDK/HTTP消息不回显，详见[API](../planning/api-draft.md#显式放弃遗留运行t-15-第三批)/[ADR-0029](../decisions/0029-offline-run-abandonment.md)。[合成SDK/stream例](../../examples/abandonment_offline.py)与[本批验证](../validation/t15-abandonment.md)不替代备份/恢复/barrier、迁移或授权导出。

T-15一致备份实际接口为SQLiteMaintenance.backup/inspect_backup/restore/get_recovery_barrier，BackupManifest/RestoreResult/RecoveryBarrier及独立[恢复扩展DDL](storage-recovery-schema-v1.sql)由源码生成。原数据库版本不变，新副本发布前写屏障，原SDK拒绝新扩展；完整语义见[ADR-0030](../decisions/0030-quarantined-consistent-backups.md)，通过范围见[验证](../validation/t15-backup.md)。本批未提供通用自动解锁；迁移/授权导出的后续实际接口见下文。

T-15收尾实现SQLiteMaintenance.preview_migration/migrate、MigrationPlan/MigrationReceipt和独立[迁移回执DDL](storage-migration-schema-v1.sql)，范围固定为历史v1兼容字段；Runtime.export_run返回RunExport/ExportedArtifact，默认元数据、正文/文件显式授权、应用redactor只改JSON副本。源定义见[ADR-0031](../decisions/0031-compatible-v1-record-migration.md)/[ADR-0032](../decisions/0032-authorized-portable-exports.md)，实际范围见[验证](../validation/t15-migration-export.md)。恢复屏障还保护cancel/新data_abandoned不误写prepared未执行。

# 当前进度与下一轮

更新：2026-10-15（Asia/Shanghai）。

## 当前状态

- 阶段：P1至P4、T-01至T-13（含T-10A/T-10B/T-10C）已完整验收。**T-14本地工作台正在实施/验收，未提前关闭整卡**；T-13历史源码26b97ee已推送，新增65项HTTP测试（62传输/离线+3真实Docker）。最终两解释器含Docker全库1132+61skip /1134+59skip通过；同一源码的核心10与Docker4个远程job全部success。SSE认证期间撤权已复现并修正，具体失败/构件/平台证据见 [本轮验证](../validation/t13-http.md)。
- 基线：[P4-2026-10-13](implementation-baseline.md)，API/运行契约 v3；T-08 落地见 [ADR-0012](../decisions/0012-transactional-memory.md)，首版沙箱前移见 ADR-0013，最新文件/产物与可选 E2B 决定见 [ADR-0016](../decisions/0016-sandbox-artifacts-and-backends.md)。历史 fingerprint 不覆盖，源变化由 ADR/验证说明。
- 已实现：包 0.1.0a0、类型/schema/模型协议、内存/SQLite 事务存储、Runtime 所有权、Python 只读工具/权限/有界线程、Agent/SDK 流/取消/历史/限额、上下文选择/预览/来源清单；内置 Memory 三工具的原子执行/CAS/墓碑、作用域查询、限时检索与子预算/固定快照；保存的原工具计划、持久审批/输入、HumanInput、respond/resume、同租户授权查看/响应及不可变回执；以及外部单次写入/unknown、核查、verify、分页启动扫描与 recover。
- 已接通沙箱：PythonSandbox/DockerSandbox/SandboxLimits/SandboxResult、固定 backend/Run/审批/恢复绑定、code/输入/输出与稳定执行/清理租约、实际 Linux Docker Engine 执行、有界文本/文件/PNG、可靠暂存/确认销毁/ready 与原结果共同发布、授权下载及原阶段恢复。T-10B 已补齐受测文件/资源/故障与 SDK/stream 硬终止、Linux AMD64 固定分析镜像/专项CI/安装。T-10C已验收可选E2B固定profile的真实执行/CSV/PNG、隔离/资源、故障/崩溃、累计费用预留与安装兼容。OpenAI Responses 官方客户端离线适配已实现，真实模型尚未验证；MCP stdio固定发现/原计划审批/受控调用/断连与进程清理已验收；T-13的HTTP/身份/SSE/上传/授权附件已验收；工作台代码已接入，最终构件/平台验收进行中；未验收：T-14至T-18，维护尚未开始；多 Agent/Skill/持久工作区等后续，无空壳。
- 本轮T-14仅本地合成身份/文件/模型输出、真实loopback HTTP/SSE及原Docker环境；未读真实Key、调用付费模型/云、上传私有资料或公开发布/部署。T-10C历史云授权/预留与边界见其验证记录，本轮未动用该云预算。
- T-08 实施起点（历史）：`/Users/yanlong/projects/drov`，main / adc9793，工作区干净且比 origin/main 多一个 T-06/07 提交；先重跑 T-07 的 343 passed / 2 skipped 后实施 T-08。保持同一 checkout，保护品牌、uv.lock、历史基线和 SQLite DDL。实施 d997f17、跨平台检查修正 40160ff/5ff654c 已提交并推送；最终补记仅修改进度/验证文档，实际 HEAD 以 git log 为准。
- 历史范围澄清（已由本轮用户要求替代）：T-08 交接时按旧 C-17 将沙箱说明为首版之后；当时仅修正状态表述。2026-10-14 用户确认首版“生成并执行代码”，现以前移后的 C-17、ADR-0013、T-10A/T-10B 为准，不能沿用旧后续排期。
- 2026-10-14 首版范围文档轮起点（历史）：同一 `/Users/yanlong/projects/drov`、main / 8f3c5e0，工作区干净且与 origin/main 同步。本轮仅修改规划、协作/开发说明与验证文档；未改 SDK 源码、测试、schema、依赖锁、DDL 或品牌，未运行/拉取容器或调用模型。文档检查点以本轮 Git 提交和下述验证记录为准。

## 已确认方向

Drov 是 Python 通用 Agent 框架，主要能力基线为 AgentScope 2.0；三个参考应用检验通用性，不限定业务。渐进 API、统一运行机制、可检查上下文/记忆是待验证的优势假设，不把类型、存储或生命周期测试写成完整 Agent 能力或体验领先。

## 本轮交付：T-14 本地工作台（验收中）

起点main/cf6c8cd，干净且远端同步；前置26b97ee的核心10/Docker4 jobs已实查success。按[ADR-0026](../decisions/0026-local-workbench.md)接通同源登录/固定资源/认证目录、会话与运行/上下文/交互/取消恢复/事件重连、原上传和安全PNG/附件。页面接入发现合法会话ID的路径表示缺口，新增查询入口与原函数共用授权；原Runtime/DDL/uv.lock/P4和品牌原素材不改。最终本地23项Playwright（4协议/19浏览器，含真实Docker）全部通过，Python HTTP+资源/查询72项通过。两解释器全库/最终构件/安装与CI核验尚在收尾，**整卡未关闭，不能进入T-15**。实际失败修正和证据见[本轮验证](../validation/t14-workbench.md)。

无真实Key/付费模型/云调用或公开发布/部署。当前无需用户新增仓库、账号、model ID或预算；保留Docker和原私有数据。完成最终检查后再更新下一项。

## 历史验收：T-13 HTTP 与身份边界（完整验收）

起点 main /119de61，工作区干净且与远端同步；T-12最终两套CI及T-10B前置已复核。交付 [DrovApp](../../src/drov/server/app.py)、同内核路由/可信身份/SSE、Runtime会话分页、私有有期限上传与Run接收同事务消费、动态授权附件；新增upload_schema=1独立扩展，原records/Artifact/Sandbox DDL不改。接口/源契约/案例/质量/兼容/schema/OpenAPI与 [ADR-0025](../decisions/0025-http-identity-and-uploads.md) 同轮同步。

前置1067+61skip含Docker通过；新增65项HTTP（62项身份/上传/恢复/真实loopback/背压及三场景9组对照、3项真实Docker CSV→CSV/PNG→销毁后HTTP/hash读取）。最终完整收集1193；两解释器含102项真实Docker全库3.11 **1132+61skip（301.31s）**、3.14 **1134+59skip（324.35s）**通过。两套构建/sdist重建、四套core-only安装、两套独立server安装及实际HTTP/SSE三场景、Twine/静态/格式/严格类型/schema/OpenAPI通过。最终26b97ee的 [核心38746896113](https://github.com/drov-ai/drov/actions/runs/38746896113) 10个job与 [Docker38746896088](https://github.com/drov-ai/drov/actions/runs/38746896088) 4个job全部success；八套支持平台HTTP/OpenAPI及安装均通过，Windows文件层适用跳过有明确记录，不冒充POSIX文件支持。**T-13整卡完整验收并关闭，无剩余任务卡缺项。** 失败修正、构件hash、命令与日志见 [本轮验证](../validation/t13-http.md)。

本轮仅合成文件/身份、本地loopback与原Docker，没有读真实Key/付费模型/云调用/公开发布或部署。暂存TTL只拒绝新消费，回收/备份尚属T-15；仅单worker和已知长度原始文件上传，LocalArtifacts字节层沿用POSIX限制；工作台尚未实施。本地按Drov归属标签清点执行容器0，历史字节/租约/台账保留。现在不需新仓库、账号、Key、model ID或预算；保留原Docker和`.drov`/私有env/SQLite/bytes/租约/云台账。下一项T-14与完成条件见文末；建议New Chat，继续当前Local checkout，先核对本节最终源码与CI。

## 历史验收：T-12 MCP stdio（完整验收）

起点main/d7dadca、工作区干净；前置T-10B/T-11/T-10C状态与实际CI已复核，含Docker前置1001+61skip通过。按 [ADR-0024](../decisions/0024-mcp-stdio-tools.md) 接通固定官方SDK2.3.0、显式短暂发现/运行连接复核、命名/schema/协议/配置指纹、同一权限/持久审批/原计划与单次派发、owner task的断连/超时/取消与关闭。无DTO/schema/DDL/品牌或历史P4指纹修改；mcp extra只精确固定已锁版本，核心导入/构造不加载SDK。

新增66项MCP，完整extras1128；最新系统环境修正后的本地全库3.11 **1067+61skip（282.79s）**、3.14 **1069+59skip（304.67s）**，均含99项真实Docker。两套MCP专项66项、构建/四套core-only安装、两套独立mcp安装后实际普通/stream及E2B离线probe通过。初期测试1s启动期限、旧Memory10ms次步断言、Windows空OS环境、PowerShell多命令false-green、8.3路径probe及异步退出通知问题均有失败日志与针对修正；当前CI独立步骤真实检查，不能引用旧Windows绿色label冒充通过。详见 [验证](../validation/t12-mcp-stdio.md)。

最终源码/测试/工作流检查点 **3a013be** 已推送origin/main；核心10与Docker4 jobs全部success，Linux3.11–3.14、macOS/Windows3.11/3.14的MCP各66项及安装均通过，Windows进程树需在5s内实查退出、超时仍失败。**T-12整卡已验收并关闭。** 收尾仅Markdown补记，以skip ci保存；实际HEAD/同步以Git为准。本轮无真实模型/私有MCP服务/云调用，stdout限额为解析后检查；只支持API/ADR列明的initialize握手、schema与text/JSON子集，不把MCP工具当Docker沙箱。

下一项T-13，需同一Runtime的DrovApp/身份/运行交互/inspection/SSE及文件授权与SDK/HTTP对照通过。当前无需新仓库、账号、Key、model ID或预算；本地服务/身份/上传夹具由执行者准备，保留Docker Desktop及原`.drov`/私有环境。建议 **New Chat实施T-13**；真实模型资源到T-16前再确认。

## 历史验收：T-10C 可选 E2B（完整验收）

起点 Local `/Users/yanlong/projects/drov`、main/e5f0a16，干净。T-10B/T-11完整验收已核对，前置含Docker复跑957+2skip后按用户明确选择插入T-10C。账号/本地凭据、合成上传和费用已获授权，无需额外用户材料；实际文件`.env.local`已确认忽略并收紧600，未输出Key。

按 [ADR-0023](../decisions/0023-e2b-single-execution.md) 接入真实E2BSandbox/E2BLimits、固定SDK/模板build与VM/工作负载profile；默认审批、原键/清理/文件/PNG/ready发布沿同一Runtime。独立保守费用与派发同事务累计、不退款；丢创建应答查空继续unknown。显式云绑定启用扩展v2以拒绝旧SDK，Docker-only保持v1和旧JSON字段。API/源契约/案例/质量/许可/安装/CI同轮同步，历史P4指纹与品牌保留。

真实SDK/stream CSV→CSV/PNG→销毁后授权/hash读取、权限/网络/资源/文件/取消/过期、远端故障、SDK/stream强制终止及新进程恢复均已通过。新增103项：44项离线、59项真实远端；完整extras收集1062项。含Docker本地全库检查点3.11为1000+59skip、3.14为1002+57skip，后续新增云测试已分别真实通过；不将该检查点冒充最后清理修正后的全库重跑。实际命令、失败修正和覆盖边界在 [本轮验证](../validation/t10c-e2b.md)。

收尾审查修正了E2B坏控制应答阻止原资源清理的问题：执行前严格拒绝代码，清理保留严格归属/ID检查；最终44项离线及40项真实故障/硬终止回归通过（含新增0代码/确认销毁例）。两解释器最终wheel仓库外安装、SDK/stream实际云执行/下载通过；旧T-11 wheel实际读取新Docker v1、拒绝E2B v2且不改库。严格类型/schema/核心安装隔离、最终763c25d的核心10与Docker4 jobs全部success，**T-10C整卡已验收并关闭**。源码/测试/锁/工作流已推送main，收尾仅Markdown补记，以skip ci保存；实际HEAD/同步状态以git log/status为准。

最终云清点：活动/暂停实例0，本次Drov实例0，固定模板仍私有。两次构建及170次沙箱寿命的保守预留合计 **4.831美元**，低于自定5美元上限；未查询实际账单/余额/赠金资格，不能将预留当扣费。区域仅按截图声明us-west-1，SDK不能证明驻留；真实模型质量尚未验证，传递原生依赖的许可元数据遗漏已记录供T-18复核。

当前不需要新仓库/账号/模型ID/预算，保留`.env.local`、固定template2记录和`.drov`原台账/SQLite/bytes。建议New Chat实施T-12，届时本地stdio服务夹具由执行者准备，无需模型Key或预算。

## 历史验收：T-11 OpenAI Responses 离线适配

起点 Local `/Users/yanlong/projects/drov`、main / aeea557，干净且远端同提交；T-10B最终两套远程CI success复核后，前置本地含Docker复跑895+2 skip。没有上项未验收缺口，直接实施T-11。按 [ADR-0022](../decisions/0022-openai-responses-adapter.md) 交付实际可选适配器：显式model/capabilities、惰性自有/借用官方客户端、工具名/严格schema、流/非流完整计划、原位置provider bundle往返、reasoning/phase、用量/脱敏错误、单层重试/Retry-After与关闭。模型ID/输出上限进入公开配置指纹，旧适配器不变；接口/运行/工程/质量/案例/依赖说明同轮同步。

新增62，全库959；最终本地3.11 **957 passed /2 skipped，214.91s**，3.14 **959 passed，228.06s**，均含99项真实Docker（其中新增2项Responses→审批→实际Python/CSV→销毁/hash读取→同一适配器）。新增项具体分类见验证记录，不将脚本服务输出当作真实模型质量。静态/类型/schema、两解释器wheel/sdist与重建比对、四套核心安装/六示例SDK+stream、两套独立openai extra安装/仓库外SDK+stream、Twine均通过。完整失败/修正、构件hash与实际日志维护在 [T-11验证](../validation/t11-openai-responses.md)。公共DTO/schema、SQLite DDL/扩展、uv.lock、历史P4指纹、品牌未改。

**T-11 整卡已完整验收并关闭。** 源码f87b595已提交/推送main，核心10 jobs及原生Linux Docker4 jobs均success；每套Docker99项全部实际通过。核心extra阶段POSIX59项，Windows57+2个POSIX字节适用跳过；八套已支持平台安装后的真实官方客户端/本地HTTP示例均通过。最终补记仅Markdown，以skip ci保存；源码/测试/锁/工作流之后无变化，实际HEAD以git log/status为准。没有读真实Key、调用付费模型/云、发布包/镜像或部署。自有/借用客户端与模型能力均有离线证据，未列任何已通过真实服务的模型ID。

下一项 **T-12 MCP stdio适配**：按官方SDK/锁实现分页发现、固定名称/schema与revision、受控调用、断连/超时/退出清理；本地真实stdio server的成功/错误/卡住、审批不绕过、关闭所有权和安装隔离均通过才关闭。现在无需新仓库/账号/模型ID/预算；确定性stdio夹具由执行者准备，若要接特定现成MCP服务，届时只需提供仓库/启动命令和允许能力，不提前索取凭据。T-16 smoke前再确认服务、可访问固定model ID、能力来源/上下文窗口和费用上限；Key只在本地环境或宿主借用客户端安全配置，不贴聊天/入库。建议 **New Chat**，继续当前Local checkout，原因是本轮模型协议已形成独立验收节点，下一轮切换进程集成模块。

## 历史验收：T-10B 文件隔离、资源与故障完整验收

起点 main / dbd2a40，干净；前置 T-10A 复跑802+2 skip、最终CI核对后实施。按 [ADR-0021](../decisions/0021-sandbox-fault-boundaries.md) 与任务卡完成真实输入/宿主/网络哨兵、CPU/OOM/PID/盘/文本/文件/路径竞态、取消/脱离组子进程、受控创建/执行/导出/销毁丢应答、SQLite/ENOSPC/丢文件/hash损坏，以及58个SDK/stream进程/保留关闭边界测试。完整原暂存可以恢复；未确认清理继续占容量和锁；丢失/损坏/超限输出不可重算或发布。

修复监督文件启动竞态、恢复成功事件遗漏原result、恢复绕过原结果限额、文件接收OSError未映射PersistenceUnavailable；另补 MinAPIVersion 必须接受固定API1.41的启动前检查。接口/源契约与案例断言同轮同步；未改公共DTO/schema、DDL/扩展版本、runner/hash、uv.lock、品牌或历史P4指纹。全部失败与修复、实际命令/计数、构件hash、LinuxAMD64固定wheel/许可/镜像与远程/本地差异维护在 [专项验证](../validation/t10b-sandbox-faults.md)。

**T-10B 整卡已完整验收并关闭。** 最终源码9a4fba1与14项远程job均通过；本次测试容器查询无残留，示例原SQLite/bytes/ref与阶段台账保留。非ready/未知阶段不自动回收，完整维护/备份仍为T-15；未开始真实模型、HTTP/UI/MCP或可选E2B。没有模型Key/私有数据/付费调用、镜像或包发布/部署。后续文档验收补记以skip ci保存，实际HEAD看git log/status。

下一项 T-11 OpenAI Responses 离线适配；建议 New Chat，仍选当前 Local checkout。现在不需要新仓库/账号/Key/model ID/预算；真实smoke前才共同确认服务、可访问ID、安全凭据配置和费用上限。

## 历史验收：T-10A Runtime 实际执行与持久产物

接续已保存的 main / 0780093 控制预检检查点；前批 [CI 38553661046](https://github.com/drov-ai/drov/actions/runs/38553661046) 10 jobs success 已复核。首个 Runtime 提交 cbce832 的 [CI 38570634938](https://github.com/drov-ai/drov/actions/runs/38570634938) 10 jobs success；之后核查审计/恢复前权限修正已重新通过本地全库/安装，最终修正 [2d768f7](https://github.com/drov-ai/drov/commit/2d768f791a4c3ee754426c8a8245d11c8778d921) 的 [CI 38572351467](https://github.com/drov-ai/drov/actions/runs/38572351467) 10 jobs 全部 success。本批实际代码见 [ADR-0020](../decisions/0020-runtime-sandbox-execution.md)，全部验证/失败修正/限制见 [本轮记录](../validation/t10a-runtime-sandbox.md)。没有更换 checkout、重新规划或修改品牌/uv.lock/P4 指纹。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 公共入口/绑定 | 构造无 I/O；Runtime owned/borrowed/factory、SQLite/LocalArtifacts 要求、不可变实际 backend hash，无沙箱旧摘要/payload 保持；普通核心导入不需 Docker |
| 审批/执行 | 专用 write/default ask、代码/服务器解析输入/输出/归属绑定；同事务预算/active/create intent；原 container/exec ID/start intent 持久化，实际执行单次，无宿主回退/自动代码重试 |
| 输入/导出 | 保持 /work/inputs/<id>/<name>；可信/代码不同非 root UID、保护输入祖先；安全声明集合导出/有界 PNG profile；缺失/链接/FIFO/超限/畸形 PNG 无引用，保留真实 exit |
| 发布/恢复 | 真实观察与字节可靠暂存→确认整个容器销毁→同事务 ready/原结果/event/checkpoint/释放租约；SQLite trigger 全回滚后 recover 原阶段，execute 计数不增；verify 共用权限/限时/真实调用所有权和核查审计，recover 清理前动态授权/终态复核；人工核查不能伪造资源事实 |
| 真实 SDK | 标准库 CSV 与分析 numpy/pandas/matplotlib 的 CSV/400×300 PNG，普通/stream、同一 Agent 最终输出、关闭后新 Runtime 授权/hash 下载通过；实际两次独立调用/显式 Ref 复用、已启动后取消清理通过 |
| 本地测试 | 45 新增，总804；含真实 Docker：3.11 802+2 skip /3.14 804；核心无配置：793+11 skip/795+9 skip，9 个 Docker 未跑不能当通过；静态/schema/包安装最终汇总见验证 |
| 验收与未做 | 本批四套包安装、两套 core-only wheel 安装后 8 次真实 Docker normal/stream、最终修正10项远程核心CI均通过；T-10B 全深度隔离/资源/逐边界硬终止与 Linux Docker CI、T-15 完整维护/备份、T-16 真实模型仍未验收 |

**当时 T-10A 整卡完整验收并关闭，下一项为 T-10B；该卡本轮已验收，见上方当前状态。** 不将基础矩阵当作 T-10B 整卡；未知应答/销毁保留租约/容量，原 SDK 输出丢失不能重算。仅合成数据与确定性模型夹具，无新 Key/账号/预算、云调用、镜像推送、包发布或部署。

## 历史检查点：T-10A 内部 Docker 控制与预检

起点 Local `/Users/yanlong/projects/drov`、main / d45bd24，干净。复跑前批 683+2 skip 后实施私有 Docker 控制/预检、不可变限额/profile/配置 hash、固定创建 argv/实际配置与有效控制检查；74 项新增和两套全库本地通过，标准库/分析镜像各两次 sum=5050、独立目录与实际销毁，决定见 [ADR-0019](../decisions/0019-docker-control-preflight.md)，失败修正/范围见 [验证](../validation/t10a-docker-controls.md)。源码模块是真实内部实现，没有导出尚未接通的公共沙箱工具，CLI 结束不冒充容器已停止。

**仅验收预检/控制子范围，T-10A 不关闭。** 代码审批、持久租约、任意代码真实结果/输入保护/安全导出与最终发布仍缺。用户在本轮要求继续未完成部分，因此保存本检查点后继续本会话接通 Runtime；保持 Docker Desktop 运行，无新仓库/账号/Key/模型 ID/预算。新远程状态仅凭实际 CI 追加，旧 CI 不代表本批。

本地检查点构件：两解释器 wheel/sdist/重建与四套干净核心安装、六示例普通/stream、Twine strict 通过；独立 3.14 core-only wheel 安装再执行两次真实控制探针且确认销毁。公开 schema/DDL/uv.lock/品牌未改；文档 59/483 链接/14 AST 错误 0，实际源码与本机日志以本轮验证为准。

## 历史第二批：T-10A Runtime 文件输入与授权读取

起点 Local `/Users/yanlong/projects/drov`、main / 412e39b，干净；复跑首批 649 passed / 2 skipped 后实施。决定见 [ADR-0018](../decisions/0018-artifact-input-admission.md)，详细证据与失败修正见 [本轮验证](../validation/t10a-artifact-inputs.md)。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| Runtime 资源 | SQLite + LocalArtifacts owned/borrowed/factory 配对、排他与关闭；普通核心/导入无 Docker，临时/内存/无 store 文件输入预先拒绝 |
| 输入/去重/授予 | start/run/stream 规范有序清单；lookup 在容量/新字节之前；私有可靠暂存后 same SQLite transaction 保存 ready 输入/Run/授予/去重/检查点/事件，失败全回滚 |
| 元数据与读取 | get/read_artifact 动态 read/view_content、ID 或固定 Ref；同 tenant 显式共享、默认不跨用户/Agent；读中撤权无字节，损坏提交 unavailable，未发布阶段不可查/下载 |
| 上下文/兼容 | 描述/hash/虚拟位置在保护预算中，无字节/宿主路径；文件 ContextManifest v2，无文件 v1，新版兼容旧库/v1；显式 Artifact 扩展 v1 不改 records DDL，旧 SDK 拒绝扩展库 |
| 实际验证 | 新增 34，全库 685；3.11 683+2 skip，3.14 685；Ruff/mypy 87 files/schema、两解释器 wheel/sdist/重建/四套干净核心安装、六示例普通/stream、Twine 通过。首次 3.14 检出原生连接夹具未关闭，修正后全库复跑通过 |
| 边界 | 接收后首次派发前文件丢失为 Failed/artifact_unavailable，模型 0；示例只是 SDK 接收/重开/hash 下载，模型仅元数据脚本，不是真实 Python 分析/输出导出或模型质量 |

**第二批输入/授权子范围验收，T-10A 整卡不关闭。** 数据扩展/已开始私有阶段与配对字节必须保留；尚无完整维护回收、输出发布、代码资源租约或恢复验收。保留首批 image ID/锁/许可，已复核当前 Local 镜像仍在；本批未创建执行容器、调用模型 API/云服务、发布包或部署。

## T-10A 私有字节层与固定分析镜像（历史首批）

起点 Local `/Users/yanlong/projects/drov`、main / 67ef5b2，干净且与 origin/main 同步。前置复跑 T-10 的 611 passed / 2 skipped，确认 T-10 已完整验收；实际 Docker/Desktop、基础镜像、显式 seccomp 均复核。决定见 [ADR-0017](../decisions/0017-private-artifact-staging.md)，详细命令、失败修复、恢复与未跑项见 [验证](../validation/t10a-artifact-staging.md)。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 值类型与私有存储 | ArtifactInput/Ref/Info/Limits、LocalArtifacts；构造无 I/O，POSIX 私有目录/排他锁/双向 SQLite 配对；旧 DDL/record schema=1 不变，metadata 只新增配对键 |
| 容量与可靠暂存 | ID 路径、写前持久预留、清单与文件 fsync/目录同步、跨重开计量；字节/长度/hash、链接/FIFO/权限拒绝；取消保留实际 worker；暂存不是 ready，没有公开上传/下载 API |
| 分析镜像 | 实际下载/校验 12 个 Linux ARM64 / CPython 3.11 wheel，固定 lock/来源/许可；离线 Docker build，本地不可变 image ID 与 RepoDigests 区分；SDK 不安装这些库，不改变 uv.lock |
| 真实可信探针 | numpy/pandas/matplotlib 实际 CSV → 统计表/400×300 PNG；受限容器有效 seccomp/cgroup/非 root，确认销毁后字节暂存、重开按 hash 读回；**不是 Agent 循环、安全导出或授权下载验收** |
| 本地检查 | 新增 38，全库 651；3.11 649 passed / 2 skipped，3.14 651 passed；静态/类型/schema、两解释器构建与四套干净核心安装/字节层检查通过；最新构件与远程状态见验证记录 |
| 保护与未跑 | 品牌/P4 指纹/uv.lock/T-10 证据不变；无 Key、模型/云调用、镜像推送、PyPI 发布或部署；T-10B 隔离/资源/清理故障矩阵与 Linux Docker 专项 CI 未跑 |

**已验收任务卡首批“镜像与 Artifact 字节配置”子范围；T-10A 整卡仍进行中。** 尚未交付 Runtime(artifacts=...) 装配、start/run/stream 输入保存/授予/去重与动态 Authorizer、PythonSandbox/DockerSandbox/结果、配置指纹/审批/稳定执行键、输入保护/安全导出、SQLite ready 引用/原结果共同发布、清理租约和恢复。不得把当前 trusted probe 直接包装成任意模型代码执行器。

本地镜像和探针文件仍仅在 `.drov/` 与当前 Docker Engine；实际源码/锁/许可在仓库 `sandbox/`，换 checkout/机器需按 recipe 重新准备并核实实际 ID。未完成台账不可读，缺清单/未知临时项 fail closed，保留配对目录及数据库，禁止手工删残留后宣称恢复。下一批在同一 T-10A 接入 Runtime/输入，再推进审批执行与发布；无资源阻碍，不需要用户额外账号/Key。

## 文件/产物必交与 E2B 可选规划同步（历史）

起点 Local `/Users/yanlong/projects/drov`、main / fa2b671，干净且与 origin/main 同步；先核对 AGENTS、progress、导航/handoff、已有源码/最新 T-10 与准备/范围审查证据。用户本轮采纳推荐范围，工作决定见 ADR-0016，完整文档检查与未跑项见 [本轮验证](../validation/2026-10-14-sandbox-artifacts-scope.md)。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 必交范围 | T-10A/T-10B 加受控文件输入、固定分析镜像、持久 CSV/PNG 等产物与授权读取；仍单次执行，不共享变量/工作区，不挂主机目录/联网安装 |
| 可选适配 | 新增 T-10C E2B，依赖 T-10B，独立实验性 extra；不阻塞 Docker 必交链，真实远端未验收不能声称支持或关闭整卡 |
| 契约与验收 | 输入/hash/授予/审批、私有有界字节、导出暂存/ready/销毁/结果一致性、B-18、原三个代码案例/X-03、HTTP/UI/维护备份/安装发布同步；正式模型任务数量未变，E2B 费用单独授权 |
| 导航与交接 | 产品/能力/API/运行/架构/工程/质量/任务/发布、AGENTS/开发/例子/reference/导航/handoff 同步；新会话 prompt 加入新的 T-10A 边界与镜像准备责任 |
| 验证与保护 | 53 个 Markdown/436 链接/14 Python AST/86 表格错误 0；21 卡/31 边与图一致、无环，可选卡不阻塞必交；映射/算术、差异与敏感模式通过。源码/测试/schema/DDL/uv.lock/品牌/历史 fingerprint 不改，不跑容器/安装/模型/E2B |

**完成的是规划同步，T-10A/T-10B/T-10C 都未实施、未验收。** 当前 Docker 基础准备仍有效但需要下一轮复查；原标准库镜像没有分析库，数据镜像在 T-10A 由执行者准备，不能从旧探针/CI 推断文件或云能力。无需用户此刻开云账号、找 digest 或提供 Key。

## T-10 故障与取消矩阵（历史整卡验收）

起点 Local `/Users/yanlong/projects/drov`、main / 53cee41，干净且与 origin/main 同步；复跑 T-09 的 491 passed / 2 skipped 及全部静态/类型/schema 后实施。完整矩阵、命令、失败调查与适用限制见 [本轮验证](../validation/t10-faults-cancellation.md)。

| 交付 | 实际结果 |
| --- | --- |
| 真实 SDK 硬终止 | 11 个语义提交边界前后、SDK/stream 接收；独立服务写成功回包前、模型响应未提交、原两调用部分成功及 Memory 事务硬终止；新进程恢复，已发生写入不重做 |
| 提交故障 | 同 11 边界的提交前/后错误；SQLite 自身实际拒绝 COMMIT；实际 SQLITE_READONLY/SQLITE_FULL，无未提交成功事实；解除故障后显式恢复/核查 |
| 取消/观察/回执 | 两后端五组完成取消次序、部分计划取消、wait/流断开；接收事务前后取消调用方与显式 cancel；20 次重复响应/取消，原回执与成功事实保留 |
| 所有权与预算 | 非协作真实线程/Task 均有释放/硬终止路径；ShutdownIncomplete 保留槽位/SQLite 锁，另一个进程无法接管；停止后可重开；耗尽次数/活动预算不重置，未知用量不填 0 |
| 验证 | 新增 120，全库 613；3.11：611 passed / 2 skipped，3.14：613 passed；Ruff/格式/mypy 75 files/schema 通过；两解释器 wheel/sdist/重建、共四套干净核心安装、五示例普通/stream 通过；52f70e3 的 10 个远程 CI job 全部 success，准确平台计数见验证记录 |
| 保护范围 | 无 SDK/公共 schema/DDL/uv.lock/品牌/历史 fingerprint 变更；仅新增测试夹具与事项服务屏障，无 Key/私有数据/付费调用/发布/部署 |

**T-10 任务卡本地与远程已完整验收，无剩余缺项。** 测试/夹具 52f70e3 已推送，远程状态/日志保存在 .drov/t10-final-ci.json / t10-final-ci.log；最后进度/验证补记单独提交同步，不改已验证测试。模型、token/金额是脚本合成值；独立事项服务写入计数是真实本地事实，不能由此宣称真实模型质量、通用 exactly-once 或断电/设备故障安全。HTTP/MCP 生命周期由 T-12/T-13 验收；旧备份 barrier 待 T-15。

## 历史接续：T-10A 前的环境准备

用户开启 Docker Desktop 后本轮资源已可连接；执行者完成官方固定 ARM64 镜像下载、来源/许可与指纹记录，真实小型容器执行及 cgroup/非 root/只读根目录/工作区/显式 seccomp 检查。默认 seccomp=unconfined 是实际发现，已用独立官方 profile 对自建探针容器启用，未改 daemon 全局设置。全部本轮探针已停止并删除，详情见 [环境准备验证](../validation/sandbox-environment-preparation.md)。

**完成的是本地资源准备；T-10A/T-10B SDK 实施尚未开始。** 无完整恶意隔离、超限、取消或恢复验收，无 SDK/依赖/schema/品牌改动。非秘密准备文件仅在当前 Local 的 .drov/，换 checkout 不会自动带走。下一项与 New Chat 建议保持 T-10A；当前无需用户再准备额外配置或密钥，保持 Docker Desktop 运行即可。

## T-09 外部核查与恢复（历史整卡验收）

起点 main / 7809192，干净且与 origin/main 同步。复跑首批 446 passed / 2 skipped、静态/类型/schema 后补完 T-09；决定见 [ADR-0015](../decisions/0015-external-effects-recovery.md)，完整证据与限制见 [本轮验证](../validation/t09-reconciliation-recovery.md)。

| 交付 | 实际结果 |
| --- | --- |
| 外部写入 | read 保留瞬态重试，write/unknown 单次派发；回包丢失/非法结果/写成功后瞬态错误保守 unknown，不盲重试 |
| 核查响应 | applied 校验原结果，not_applied 要求旧调用停止并重新审批；unresolved 原子换新请求；取消/预算耗尽只凭已核实效果收尾，无新模型/写入 |
| verify | 原用户/operation/依赖只读查询，权限/正文复核、独立期限、一个真实 Task/槽位；超时保留调用/锁，迟到建议不能消费旧请求；终态后审计不改终态 |
| recover/扫描 | 分页 104 条跨归属遗留记录，扫描不执行；原 revision/engine/input/plan/预算复核；已提交输入/结果/最终响应直接复用，未提交响应只按原输入与剩余次数重试 |
| S-03/例子 | 独立非幂等事项进程和实际计数；五组正常/拒绝/输入/丢回包/取消各 3–4 个 SDK 新进程，普通/stream 均通过，拒绝 0、其他 1 次写入；第一批参数/身份/撤权与重复回执回归保留 |
| 验证 | 新增 49 项、撤去四个过期不支持写入断言，全库 493；本地 3.11：491 passed / 2 skipped，3.14：493 passed；静态/格式/mypy 72 files/schema 通过，两解释器各两套干净核心安装、五示例普通/stream、wheel/sdist/重建/Twine 通过；413fa1b 的 10 个远程 CI job 全部 success，准确平台计数见验证记录 |
| 保护/兼容 | 两个 verification 事件增量已生成 schema；旧操作可读，无 verifier 定义不增加指纹键；无新依赖/uv.lock/DDL/品牌/历史基线修改；无 Key、付费调用、公开发布或部署 |

**T-09 任务卡的五个 S-03 离线路径及 B-01/04/05/07 功能条件已本地与远程验收，未将 T-10 的硬崩溃专项写成完成。** 模型是 ScriptedModel，计数是实际外部合成服务事实；不代表真实模型质量或外部通用 exactly-once。原调用不协作仍可能 ShutdownIncomplete；旧备份 barrier/维护仍待 T-15。

## T-09 首批审批/输入交付（历史子范围）

起点为当前 Local `/Users/yanlong/projects/drov`、main / d9bafbc，工作区干净并与 origin/main 同步。阅读 AGENTS/本页/导航/handoff、T-08 与范围验证；前置复跑 398 passed / 2 skipped 及静态/格式/类型/schema 全通过后实施。决定见 [ADR-0014](../decisions/0014-durable-approval-input.md)，实际环境/命令/子范围证据见 [T-09 验证](../validation/t09-interactions.md)。

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 保存原计划 | 检查点引用已提交响应/稳定操作 ID；审批后不重问模型，已有结果不重派发，多调用顺序及累计 step/attempt/tool/活动时间保留 |
| 审批/回执 | 显式 Runtime 的 read PythonTool/Memory ASK 保存审批；批准/拒绝、20 次重复响应、冲突、到期、原参数/配置/归属绑定，响应/消费/状态/事件同事务；容量或缺 revision 不消费 |
| HumanInput | 固定注册 schema，合法答案原子成为原工具结果，非法答案可修正；可先审批再输入；临时入口仍 interaction_unavailable，无恢复 ID |
| 权限/取消 | 同 tenant 跨用户 Authorizer 分别授予 respond/read/view_content；执行始终原用户，撤权阻止派发或答案消费；取消竞争无重复派发，已成功 Memory 保留；调度失败保留回执及 recovery 暂停 |
| 实际示例 | interactions_offline 普通/stream，批准/拒绝各四个独立进程，重建原 revision、固定参数、实际只读结果和计数 1/0；重复回执不调用模型 |
| 本地回归 | 新增 48 项，全库 448 项；Python 3.11.9：446 passed / 2 skipped，Python 3.14.3：448 passed；静态/格式/严格 mypy（66 files）及 schema 通过；两解释器共四次干净安装/四示例普通与 stream、wheel/sdist 重建及 Twine 通过 |
| 保护范围 | 无新依赖、公共 DTO/schema、DDL/uv.lock/历史基线变化；品牌未改。外部 write/unknown 接收前继续拒绝，没有 verify/recover 或沙箱空壳；无 Key/付费调用/包上传/部署 |

**首批当时只验收审批与补充输入子范围，没有关闭 T-09；本轮完成范围见上表。** 首批交接时外部写入/核查/恢复未实现，现由本轮补齐；只读计数/Memory 事务仍不能替代外部证据。T-10 与沙箱卡没有随首批提前开始。

## 2026-10-14 首版代码沙箱范围调整（历史文档交付）

| 交付 | 实际结果 / 限制 |
| --- | --- |
| 范围与依赖 | C-17 最小 Python 单次隔离执行前移为正式首版必交；保留历史编号，增加 T-10A 闭环、T-10B 隔离/故障/恢复，接在 T-10 后；T-11 至发布路径增加相应前置与门槛 |
| 工作契约 | ADR-0013、API、运行/工程/架构同步：显式 Docker/Linux、固定可信镜像、无网络/主机挂载/凭据、有限资源；代码专用工具沿现有审批，实际执行/清理租约持久化，恢复不重跑已派发代码 |
| 案例与质量 | 原三类应用各增加代码变体及 X-03/B-16/17；离线真实容器与故障矩阵、真实模型代码子组、安装/HTTP/工作台/维护/发布验收联动；试验量与阈值只在 quality-plan 维护 |
| 状态与交接 | 导航、历史基线差异、开发/例子/已实现参考同步；T-08 Memory 说明片段修正正文读取参数，未改示例源码；接续 prompt 已足够，保留从 progress 选任务并补记用户 commit/push 授权 |
| 文档验证 | 本地链接、Python 片段语法、20 张任务卡/依赖图无环及验收映射、评测算术、Git 差异与保护范围检查，见 [范围调整验证](../validation/2026-10-14-sandbox-scope.md)；只做文档检查，不把语法/历史 CI 写成新功能通过 |

**已完成的是任务与源契约调整；代码生成/沙箱执行仍未实现、未运行验证。** 不建立公共空壳，不提前关闭 T-10A/T-10B。没有选择/预装实际镜像或核实 Docker daemon、版本/资源能力，平台成绩也未产生；没有真实模型质量、费用或隔离实测。历史 P4 fingerprint 保留不覆盖，旧审查成绩不能代表新增两卡。

恢复顺序为 T-09 → T-10 → T-10A → T-10B → 后续集成/评测/发布。资源缺失时先交付不依赖资源的配置/协议子范围，并记录已验收子范围、真实后端缺项、环境准备与复跑步骤；无 daemon 的 skip/替身成功不能关闭沙箱任务或正式发布门槛。Skill 与更强/云/持久沙箱没有随本轮扩展。

## T-08 历史交付（完整验收）

| 交付 | 实际结果 |
| --- | --- |
| 事务工具 | remember/recall/forget 与结果/来源、Run 摘要、预算/检查点及 memory.changed 同事务；权限复核、CAS 冲突、重复操作、大小限额/提交失败不产生虚假成功 |
| 版本/隔离 | 新建 1、删除墓碑 2、按版本重建 3；tenant/user/Agent 查询阶段隔离，最近/精确键/NFKC+casefold 子串及稳定顺序、跨页匹配 |
| 检索与快照 | required 失败/超时模型/step 0；best_effort 迟到不用、清理有界；保存前复核/删除重建，提交后重试固定；实际消息/协议开销进入 Memory 子预算 |
| 清单/权限 | 真实版本与 opaque 来源 ID、排除原因；显式 recall 的预览来源同步标记，元数据无私有 key/content；原有正文动态授权回归保持 |
| 跨进程示例 | [memory_offline.py](../../examples/memory_offline.py) 普通/stream 各七个独立进程验证保存、跨会话读取、三个隔离组、删除及删除后读取；无网络/真实模型 |
| 文档/兼容 | ADR-0012、API/运行/工程/架构/质量/S-02/开发说明同步；旧操作可用默认空 memory_sources 读取；无新增依赖、公共 schema 或 SQLite DDL |

新增 56 项 Memory 测试，移除一个过期的 Memory 拒绝参数断言，全库 400 项。Python 3.11.9：398 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：400 passed。静态/类型/schema、两个解释器的 wheel/sdist/重建、各两个干净核心安装及三个示例的普通/stream 入口通过；详见 [T-08 验证](../validation/t08-memory.md)。

T-08 历史交付无剩余任务卡本地缺项。只保证基础 scoped 检索与本地事务，不代表真实模型记忆质量、大规模检索性能或语义检索。forget 不删除历史/输入快照；非协作后端超出清理宽限时保留任务/槽位并抛 PersistenceUnavailable，不伪造 durable 终态。T-08 当时所有 ASK 失败；本轮 T-09 已交付审批/输入子范围，外部未知效果与显式恢复仍待后续验收。

## T-07 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 完整组与来源 | 一个旧 Run 的用户输入、完整响应、工具结果和 provider_items 一起选取/排除；保护当前协议链、系统规则、schema；保存全部候选来源及实际版本/hash |
| 预算与选择 | 已知窗口扣输出预留并取显式/默认上限，未知窗口要求配置；请求 JSON/schema/provider 与协议开销全部粗估；最近组优先，装不下继续尝试更旧组，最终顺序稳定 |
| 工具预览 | 带类型、ref/version/hash/字节数/截断标记的 UTF-8 摘录；当前结果在紧预算下确定性缩小，完整引用/保护项无法装入则拒绝；原结果不改写、不重派发 |
| 原子输入/清单 | 读取候选、保存 model_input/context、事件及检查点同事务；提交失败计数 0；拒绝仍有 rejected 清单，重试保持同一输入/清单/hash |
| 查询与权限 | get_context 默认元数据、正文两次复核；provider 正文永不展示；inspect 只索引当前事件页清单，sources=[] 为摘要，详情实际可读；SQLite 重开和返回对象隔离通过 |
| 示例与契约 | [预览示例](../../examples/context_preview_offline.py) 普通/stream 入口真实工具+合成长资料，报告明确未读全文；ADR-0011/API/运行/工程/架构/质量/案例及生成 schema 同步 |

新增 [test_context.py](../../tests/test_context.py) 35 项，全库 345 项。Python 3.11.9：343 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：345 passed。Ruff、格式、严格 mypy、schema、wheel/sdist/重建、两个解释器各两个干净核心安装及检索/预览普通/stream 示例通过；详见 [T-07 验证](../validation/t07-context.md)。

T-07 任务卡无剩余本地验收缺项。估算是显式启发式，不是精确 tokenizer/费用或硬上界；选取/清单需扫描已提交会话，详细来源规模随历史增长，未承诺超长历史性能。T-06 存量输入没有清单，不回填伪造来源；新记录按保存的调用 ID 查询。只验证 macOS 本地 3.11/3.14，不把历史 CI 当本轮跨平台证据；Memory 时序的 B-08 部分仍属 T-08，真实质量仍待 T-11/T-16。正文仍仅支持 context 本人的 tenant/user，完整交互/恢复仍待 T-09。

## T-06 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 入口与接收 | start/run/wait、临时/同步入口实际执行；去重先于容量/忙；控制 Task 保留接收提交/交接所有权；不支持配置接收前拒绝 |
| 模型与工具 | 固定输入、独立 attempt、完整响应/稳定操作计划同事务；实际只读工具顺序执行、严格类型结果；ASK 明确失败且计数 0 |
| 历史与预算 | 输入事实只读一次；Session 顺序号防相同时间戳乱序；失败/取消计划有明确结果；模型/工具计数、活动预留及已知/未知用量持久保存 |
| 观察与权限 | 元数据/正文分离、便利入口预检、缓存事件/最终结果动态撤权；流退出不取消执行；有界增量/gap、durable 分页补读与当前终态追平 |
| 取消与故障 | 显式取消落盘，终态取消幂等；未停止实际模型/线程保留槽位/锁；接收/计划/派发/最终提交故障无虚假成功 |
| 示例与安装 | [离线检索](../../examples/README.md) 普通/stream 两入口；真实工具+合成资料+预设模型、Report/实际引用核对；两解释器的干净安装执行验证 |
| 源契约 | ADR-0010、API/运行/架构/工程/质量/S-01/开发说明同步；无新依赖或公共 DTO schema 变化 |

新增 53 项测试，全库 310 项。Python 3.11.9：308 passed / 2 skipped（即时任务工厂 API 不适用）；Python 3.14.3：310 passed。Ruff、格式、严格 mypy、schema、wheel/sdist/重建、干净安装后普通/流式示例通过；详见 [T-06 验证](../validation/t06-agent-loop.md)。

T-06 无剩余任务卡缺项；本地通过不代表新代码已通过远程/跨平台 CI。T-06 交付时完整历史装不下会 context_overflow、inspection.context_manifests 为空；T-07 本轮已替换为组选取/预览/实际清单。Memory/write/unknown 接收前拒绝，持久 ASK 也失败；启动扫描/显式 recover/respond/resume 仍属 T-09。当前正文读取仅 context 的 tenant/user 作用域。

## T-05 历史交付

| 交付 | 实际结果 |
| --- | --- |
| 工具定义 | @tool 产生含真实 callable 的 PythonTool，继承 T-02 schema/codec；拒绝生成器/保留名称和注册后的对象替换 |
| 权限 | 不可变 deny/ask/allow，scope/业务否决优先；OwnerAuthorizer 与 execute_tool/ToolResource；规范参数快照、排队/派发前当前权限复核 |
| 实际只读派发 | 同步/异步函数共用校验；有界线程/队列和真实 Future；ASK 不造交互 ID，write/unknown 明确拒绝执行 |
| 操作与预算 | payload schema=1；操作/Run 摘要、检查点、事件同事务；派发预算预留持久化，重试用独立 attempt ID；重复操作不自动执行 |
| 故障 | 未授权/提交失败计数 0；输出非法/超大不成功、不重试；迟到结果不用；取消未停止调用保留槽位/锁，关闭失败可重试 |
| 文档与依赖 | ADR-0009、API/运行/架构/工程/质量/案例同步；完整 T-05 验收的 T-03/T-04 前置补入任务卡；新增公开权限 schema，无新依赖 |

新增 52 项测试，共收集 257 项；本地 Python 3.11.9/SQLite 3.45.1：256 passed、1 skipped（即时任务工厂 API）；Python 3.14.3/SQLite 3.52.0：257 passed。静态/类型/schema、构建、干净安装及故障实验见 [T-05 验证](../validation/t05-tools.md)。T-05 无剩余验收子项；完整 Agent/交互与正文授权仍按后续任务实施。T-04 历史证据见 [所有权验证](../validation/t04-runtime.md)。

## GitHub / CI

- T-13：20eb133→OpenAPI跨版本修正478eb1e→最终SSE授权复核修正 [26b97ee](https://github.com/drov-ai/drov/commit/26b97ee34f4078e92cce8e25fba9d86c76bde20c) 已推送origin/main。最终 [核心38746896113](https://github.com/drov-ai/drov/actions/runs/38746896113) 10 jobs、[Docker38746896088](https://github.com/drov-ai/drov/actions/runs/38746896088) 4 jobs全部success，逐个核对head_sha；Linux3.11–3.14、macOS/Windows3.11/3.14的HTTP/OpenAPI与独立server安装实际通过，四套Docker各102项实际通过。历史478的14项成功不能替代26b的结果；本轮最终日志/跳过维护在T-13验证。收尾仅Markdown，以skip ci保存，不重新改变已验收源码/锁/工作流；当前HEAD/同步以Git为准。

- T-12：046bf6c→c786fdc→4ebedb7→Windows环境/门禁dd73a0a→最终测试检查点 [3a013be](https://github.com/drov-ai/drov/commit/3a013be1e7c8668c98def8f8a1fd5350cf8e6a71) 均已推送origin/main。最终 [核心38718551848](https://github.com/drov-ai/drov/actions/runs/38718551848) 10 jobs、[Docker38718551936](https://github.com/drov-ai/drov/actions/runs/38718551936) 4 jobs全部success；逐个核对headSha，不复用旧head。八套MCP各66项与独立安装/示例实际通过，四套Docker各99项。此前4ebedb7的Windows多命令步骤曾掩盖失败，日志复核后已作废；当前各native检查独立步骤，具体失败/修复与跳过维护在T-12验证。收尾只改Markdown，以skip ci提交，不修改已验收SDK/测试/锁/工作流。


- T-10B：fbc45b8→9ba9ce3→62a5072→最终源码 [9a4fba1](https://github.com/drov-ai/drov/commit/9a4fba12d09ffd02f194936fa2caf635b7cf6b03) 已提交并推送 origin/main。最终 [核心CI38600296733](https://github.com/drov-ai/drov/actions/runs/38600296733) 10 jobs 与 [Docker专项38600296414](https://github.com/drov-ai/drov/actions/runs/38600296414) 4 jobs 全 success；核心Docker skip和Windows POSIX skip不冒充沙箱通过。前批Docker四项也通过，但最终采用9a4fba1。验收补记仅文档，skip ci；当前HEAD/同步仍以Git为准。未提交.drov/真实凭据或改全局配置。

- T-10A 本轮完整闭环：源码 cbce832、核查/恢复修正 [2d768f7](https://github.com/drov-ai/drov/commit/2d768f791a4c3ee754426c8a8245d11c8778d921) 已推送 origin/main；最终 [CI 38572351467](https://github.com/drov-ai/drov/actions/runs/38572351467) 10 jobs 全部 success。本地两套独立解释器含真实 Docker 802+2/804、四套构件安装与两套 wheel 安装后8次真实 Docker已通过。最终验收/交接补记只改文档，以 skip ci 同步，不改已验证源码；当前 HEAD/同步以 git log/status 为准。品牌/uv.lock/历史 fingerprint/core records DDL 未改，忽略环境/私有数据未提交；CI 普通核心的 Docker skip 不算 T-10B 专项。

- T-10A 第二批（历史）：GitHub SSH 连通已恢复，原 f2f45c1/412e39b、本批代码/测试/契约 **29d225d** 与崩溃夹具修正 ef1b0b4/**e83d566** 已成功推送 origin/main，没有修改全局网络/认证。最终 [CI 38536288876](https://github.com/drov-ai/drov/actions/runs/38536288876) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14、Python 3.15 advisory 共 **10 个 job 全部 success**；八个 package job 的构建/核心干净安装/适用示例普通与 stream/extras 隔离导入通过。POSIX 3.11 为 683+2 skip、其余 685；Windows 3.11 为 610+75 skip、3.14 为 612+73 skip，Artifact POSIX 字节层 72 项在 Windows 适用跳过，不表示 Windows 支持文件配置。前两轮 Windows 失败、控制期限与诊断修正见 [验证](../validation/t10a-artifact-inputs.md)，日志在 `.drov/t10a-input-ci-{first,second,third}*`；Docker 专项与整卡仍未验收。最终进度/证据补记仅改文档，以 skip ci 保存；实际 Git/同步状态以 git log/status 为准。

- T-10A 首批交付时（历史），代码/镜像 recipe/测试/契约记录已本地提交 **f2f45c1**；提交前核对 30 个跟踪文件、敏感模式和差异，未跟踪 `.drov/`、品牌或真实凭据。**当时推送未成功，新代码远程 CI 未运行/未验收。** SSH 22 与 GitHub 官方 SSH 443 连接均被关闭；HTTPS 的已有本机代理 127.0.0.1:17890 不可连接，单次绕过该配置后 TLS 仍失败，gh API 也 EOF。没有更改 origin、全局代理/SSH/认证设置。当时远程跟踪仍 67ef5b2，本地完整检查点保留；本轮已恢复连通并同步，最新 CI 见上条，核心 CI 也不能替代 T-10B 的 Docker 专项。最后同步事实补记为本地文档提交，以实际 git log/status 为准。

- 本轮文件/产物与可选 E2B 规划检查点 6feae84 已提交并推送 origin/main，包含 ADR-0016、26 份 Markdown 与本轮验证/交接；未改源码，以 `[skip ci]` 保存，不重跑未变的 SDK 矩阵。同步事实补记仍只改文档；实际最终 HEAD 与工作区状态以 git log/status 为准。T-10A/T-10B/T-10C 均未实施，不从该提交宣称运行验收。

- 本轮 T-09 完整实施 d33fb5b、严格版本拒绝 ab241dc、跨平台夹具修正 fd3d7d5/[413fa1b](https://github.com/drov-ai/drov/commit/413fa1b35b9f4b7d3f967b83a46631a2ed0662d4) 已提交并推送 origin/main；最终 [CI 38497287077](https://github.com/drov-ai/drov/actions/runs/38497287077) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。八个 package job 的完整测试/构建、两套核心干净安装、五个示例普通/stream、extras 隔离导入通过；准确计数/三轮失败与修正见 [完整验证](../validation/t09-reconciliation-recovery.md)。最终证据补记只改文档，以 skip ci 保存；实际 HEAD 与同步状态以 git log/status 为准，无真实模型、包发布或部署。

- 历史 T-09 首批审批/输入实施 [9993500](https://github.com/drov-ai/drov/commit/99935000bee29a52fc93292a0dcb321657ff3187) 已提交并推送 origin/main；[CI 38436332419](https://github.com/drov-ai/drov/actions/runs/38436332419) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。八个 package job 的测试/构建/两套核心干净安装、四示例普通与 stream、extras 隔离导入通过；准确计数/范围见 [T-09 验证](../validation/t09-interactions.md)。本补记仅改 progress/验证文档，后续以 skip ci 保存，不重跑不变代码；实际 HEAD 与同步状态以 git log/status 为准。

- 首版范围文档轮检查点（历史）7cc0fc4 已提交并推送既有 origin/main，推送后工作区干净、与远程同步；本记录随后补记该事实。两次均为 `[skip ci]` 文档提交，不重跑不变的 SDK 矩阵；实际最新提交以 git log/status 为准，保护旧验证与品牌。

- 继续使用现有 origin 和用户已有 GitHub 身份；不修改全局代理/认证、不建立新仓库。提交前检查可识别凭据模式与 Git 跟踪范围，品牌/环境/数据库排除。
- 历史 T-08：实施 [d997f17](https://github.com/drov-ai/drov/commit/d997f17f7bcf34f6ceea170b378fab956240923b)，跨平台检查修正 40160ff / [5ff654c](https://github.com/drov-ai/drov/commit/5ff654cf0ac8044125554029acae0638bc85ca6a) 已推送 main（同时包含 T-06/07 的 adc9793）。最终 [CI 38413817284](https://github.com/drov-ai/drov/actions/runs/38413817284) 的 quality、Linux 3.11–3.14、macOS/Windows 各 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success；测试、构建、核心干净安装/三个离线示例、extras 隔离导入通过。两轮失败原因、适用跳过和恢复证据见 [T-08 验证](../validation/t08-memory.md)。
- 最终补记为文档提交，不更改已验证代码；不再重复运行矩阵。当前无 T-08 本地缺项或远程 CI 失败，未做真实模型评测、包发布或部署。
- T-04 实现提交 [45a57ea](https://github.com/drov-ai/drov/commit/45a57eab30b633889f625e2cde80b00c6b532205) 已推送 main；[CI 38387899628](https://github.com/drov-ai/drov/actions/runs/38387899628) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14 和 Python 3.15 advisory 共 10 个 job 全部 success。测试/构建/干净安装/extras 导入通过；3.11 即时任务 API 和 Windows 打开文件替换用例的适用跳过见 T-04 报告。
- T-05 实现 d84c66f 与最终修正 [ecc734b](https://github.com/drov-ai/drov/commit/ecc734ba595a43579172d7631153925052962208) 已推送 main；[CI 38396712936](https://github.com/drov-ai/drov/actions/runs/38396712936) 的 quality、Linux 3.11–3.14、macOS/Windows 3.11/3.14、Python 3.15 advisory 共 10 个 job 全部 success。实际测试数量/适用跳过及构建/安装/extras 证据见 T-05 验证。
- 随后补记 CI 的文档提交不改变已验证代码；工作区检查点和交接完整，无 T-05 验收缺项。
- 历史：[T-03 CI 38345105142](https://github.com/drov-ai/drov/actions/runs/38345105142) 对 751ff55 成功；平台/跳过项见 [T-03 验证](../validation/t03-storage.md)。

## 下一项：T-14 本地工作台

T-13前置已完整验收。按 [任务卡](implementation-plan.md) 实施本地工作台，复用现有HTTP schema、原品牌素材与同一Runtime，不重开一般规划，不建立第二套Agent循环。

1. 实现页面、会话/Run/上下文检查、审批/输入/核查、取消与SSE断连重连；静态资产纳入wheel，并验证仓库外安装可使用。
2. 接通受控上传、代码/输入清单、有界文本、退出/清理状态与产物列表；安全预览PNG，附件经已有授权接口下载。权限不足时不显示正文/文件名，HTML/SVG不自动内联执行；查看/下载不重新运行代码。
3. 三个参考应用、真实Docker CSV/图表、拒绝/失败/重连须在页面可操作，原审批/取消/归属边界不被绕过；UI自动检查、安装后资产/入口与必要核心回归通过，才关闭T-14。离线合成模型足够，不依赖付费模型。
4. 当前无需新仓库/账号/Key/model ID/预算；执行者准备本地页面验证环境，保护已有品牌素材。保留现有Docker Desktop和`.drov`数据；T-16 smoke前才另行确认真实模型服务、固定ID、能力依据与费用上限。

## 用户准备事项

| 事项 | 当前状态 / 需要时点与配置方式 |
| --- | --- |
| T-10 故障与取消 | 本轮矩阵不需要新增仓库/账号/Key/私有资料；独立合成服务/真实 SDK 屏障、SQLite 错误与恢复已本地与远程验收并推送，无需用户新增资源 |
| GitHub 仓库 | 继续现有 origin/身份；本轮连接已恢复，原检查点已同步，不需新仓库/账号/Key。完整闭环最终修正与 CI 的实际状态见上方 GitHub 记录；无需用户改变全局配置 |
| T-10A/T-10B Docker/镜像/文件存储 | 保持 Docker Desktop 开启；标准库镜像不变，T-10A 首批已实际构建固定分析镜像并验证私有暂存，见 [本轮验证](../validation/t10a-artifact-staging.md)。不需新仓库/账号/Key/预算；T-10A/T-10B全部验收，ARM64与原生AMD64固定分析镜像/锁/许可、Linux专项/安装已通过；T-13使用原Docker验证HTTP文件闭环，T-14继续复用。`.drov/sandbox-environment.json`、分析 image.json 与台账是本 checkout 非秘密快照/数据，换机器需重查，不能从探针推断整卡验收 |
| T-10C 可选 E2B | 整卡已验收；实际`.env.local`安全显式加载，Key不显示/入库/上传。固定私有分析模板保留，T-10C收尾清点时活动/暂停实例0，本轮未查询云；当时保守累计预留4.831美元，实际余额/扣费到控制台核对。无需额外材料或继续云调用；保留账号权限/凭据/模板build和忽略的原台账。区域按截图声明，SDK不能证明驻留，强区域要求拒绝 |
| T-12 MCP stdio | 整卡已验收，无需补充材料；若以后指定真实现成服务，再提供其仓库/绝对启动命令和允许工具。凭据只在宿主安全配置，不贴聊天/Git |
| T-13 HTTP/身份/文件 | 整卡已验收，无需补充材料；实际组织身份系统在需要对接时再明确配置，无需本轮部署公网 |
| T-14 本地工作台 | 无需新仓库/账号/模型ID/Key/预算；沿用原品牌和本地HTTP身份，执行者准备页面/断连重连/附件验证。保留原Docker和私有数据；无需公开部署 |
| 模型服务、可访问 model ID、密钥和总预算 | T-16 smoke/live 前提供可访问服务/model ID 并批准共同费用上限；工程执行者先按新增代码任务量估价。Key 仅本地环境/忽略配置或凭据管理，聊天/Git 不贴密钥；未授权预算前不付费调用 |
| PyPI/TestPyPI 名称/权限、发布身份 | T-17/T-18 有具体构件时核实；到时按发布策略提供账号权限/安全发布身份，不假定包名可用 |
| Cloudflare drop.dev、账号范围、品牌来源与公开使用范围 | 官网/素材初次公开前核实；现有素材保留本地，当前不部署 |

完整发布条件仍在 [release-policy](release-policy.md)，不另建第二套范围。

## 会话切换建议

T-13已完整验收，下一轮转为前端交互与安装资产，**建议New Chat实施T-14**，继续Local `/Users/yanlong/projects/drov`。先核对实际Git、26b97ee最终两套CI及 [T-13验证](../validation/t13-http.md)，不沿用旧聊天推断状态。原`.env.local`、`.drov`的SQLite/bytes/租约/模板/费用台账继续保留。下一轮本地工作台不需要新账号、模型Key/ID或预算；T-16前再确认真实模型服务/固定ID/能力来源、安全凭据配置和调用预算。

## 阶段记录

| 日期 | 阶段 | 结果 |
| --- | --- | --- |
| 2026-10-12 | P1–P3 | 产品/API、架构/运行、工程/任务/质量/维护规划完成 |
| 2026-10-13 | P4 | v3 契约、ADR-0005、历史基线与代码就绪审查通过 |
| 2026-10-13 | T-01 | 工程骨架、锁、构建/导入/CI 配置完成 |
| 2026-10-13 | T-02 | 公共类型/schema/适配协议、116 项离线验证完成 |
| 2026-10-13 | T-03 | 内存/SQLite、CAS/去重/原子边界、锁/取消/真实进程验证完成 |
| 2026-10-13 | T-04 | 注册指纹/恢复校验接点、资源/任务/真实调用所有权、取消/容量/关闭完整验收；下一项 T-05 |
| 2026-10-13 | T-05 | Python 工具/权限、真实只读派发、有界队列/记录/重试与故障验收完成；下一项 T-06 |
| 2026-10-13 | T-06 | 只读 Agent/固定模型计划、历史/限额、正文授权、SDK 流/取消、离线检索与安装执行本地验收完成；下一项 T-07；未推送新 CI |
| 2026-10-13 | T-07 | 完整上下文组选取、确定性预算/预览、固定来源清单、授权查询及安装执行本地验收完成；下一项 T-08；未推送新 CI |
| 2026-10-13 | T-08 | 内置 Memory 原子工具/CAS/墓碑、隔离/限时检索/子预算/固定快照、七进程普通/stream 示例及 400 项回归本地验收完成；10 个远程 CI job success；下一项 T-09 |
| 2026-10-14 | 首版范围调整 | 用户确认代码生成/执行首版必交，新增 T-10A/T-10B，契约/案例/质量/发布及交接文档同步并检查；沙箱未实施，下一项仍 T-09 |
| 2026-10-14 | T-09 子范围 | 审批/输入、原子 respond/resume、原计划/预算与回执、同 tenant 授权及四进程示例本地验收；外部未知效果/verify/recover 未实施，整卡进行中 |
| 2026-10-14 | T-09 整卡验收 | 外部单次写入/unknown、verify/核查响应、启动扫描/显式步骤恢复、五组独立服务/多进程 SDK 与 stream 示例及 493 项回归；413fa1b 的 10 个远程 CI job success；T-10 硬崩溃专项尚未开始 |
| 2026-10-14 | T-10 整卡验收 | 120 项硬终止/真实 SQLite 故障/取消竞争新增；全库 613、两解释器/构建/安装及 52f70e3 的 10 个远程 CI job success；下一项 T-10A 尚未开始 |
| 2026-10-14 | 沙箱环境准备 | Docker 已连接、官方 ARM64 固定镜像预装、基础受限容器与显式 seccomp 生效；资源就绪，T-10A/T-10B 尚未实施 |
| 2026-10-14 | 首期沙箱范围审查（历史） | 用户要求核对 T-10A/T-10B 与 E2B；当时记录固定来源与建议、未改范围；其后已采纳，见下一行 |
| 2026-10-14 | 文件/产物与可选 E2B 范围采纳 | 用户采纳 ADR-0016，必交 T-10A/T-10B 加受控输入/持久产物，新增独立可选 T-10C；源文档与验收/交接同步检查，三卡均未实施；建议 New Chat 实施 T-10A |
| 2026-10-14 | T-10A 首批实施 | 私有 POSIX 字节层、38 项存储测试、固定 ARM64 分析镜像/许可锁及可信 CSV/PNG/销毁后重开字节探针；整卡进行中，下一轮继续输入接收/Run 授予/授权，不进入 T-10B |
| 2026-10-14 | T-10A 第二批实施 | Runtime 文件输入/授予/去重/动态授权读取、34 项新增与上下文 v2/Artifact 扩展兼容、六示例/安装；整卡仍进行中，建议 New Chat 实施真实沙箱审批/执行/租约 |
| 2026-10-14 | T-10A 第三批检查点 | 私有 Docker 控制/预检、74 项新增与两种镜像两次控制探针、四套安装；0780093 的 10 个远程 CI job success，整卡尚未关闭 |
| 2026-10-14 | T-10A Runtime 闭环 | 45 项新增、真实审批/代码/输入/CSV/PNG/下载/两次独立与取消、原发布恢复，本地804用例；最终独立两解释器/包/安装后8次Docker及2d768f7的10项远程CI通过，整卡关闭；下一项T-10B |
| 2026-10-15 | T-10B 整卡验收 | 新增93、全库897，两解释器/构件/安装后实际Docker；原生Linux3.11–3.14每套97专项与最终9a4fba1的核心10+Docker4 jobs成功；下一项T-11，建议New Chat |
| 2026-10-15 | T-11离线适配整卡验收 | 新增62、全库959，两解释器含Docker/核心与extra安装；最终f87b595核心10+Docker4 jobs全部成功，整卡关闭；下一项T-12，建议New Chat，真实模型未验收 |
| 2026-10-15 | T-10C可选E2B整卡验收 | 用户插入补做；新增103（44离线/59真实云）、完整收集1062；最终清理修正40项云回归、两解释器安装后SDK/stream和763c25d核心10+Docker4 jobs成功；0活动/暂停实例，保守预留4.831美元，整卡关闭；恢复T-12，建议New Chat |
| 2026-10-15 | T-12 MCP stdio整卡验收 | 新增66、完整收集1128；两解释器含Docker全库1067+61skip/1069+59skip、四套核心/两套mcp安装；修正Windows环境/原生CI命令失败判定/退出观察及既有测试问题，最终3a013be核心10+Docker4 jobs全部success；下一项T-13，建议New Chat |
| 2026-10-15 | T-13 HTTP与身份整卡验收 | 新增65、完整收集1193；两解释器含102真实Docker全库1132+61skip/1134+59skip、四套core/两套server安装与实际HTTP/SSE；修正跨Python OpenAPI与SSE认证期间撤权，最终26b97ee核心10+Docker4 jobs全部success；下一项T-14，建议New Chat，真实模型未验证 |

2026-10-15 准备轮历史补记：当时用户咨询可选T-10C材料，核对任务卡/release-policy与E2B官方quickstart/网络/价格资料；该准备轮仅文档变更，尚未安装SDK/读取凭据/调用云。其后用户明确授权本轮实施，现状以上方T-10C完整验收为准。

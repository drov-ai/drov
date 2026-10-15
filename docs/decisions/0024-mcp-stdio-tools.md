# ADR-0024：固定发现快照的 MCP stdio tools

日期：2026-10-15。状态：工作决定；实际验收范围见 [T-12 验证](../validation/t12-mcp-stdio.md)，任务状态只维护在 [progress](../planning/progress.md)。承接 ADR-0004/0008/0009/0014/0015，不修改历史 P4 指纹。

## 原因

MCP 服务定义是在连接后发现的，而 Agent 注册/审批必须在执行前固定。官方 SDK 2.3.0 的高层 Client.call_tool 还可能处理 input-required 或重新发现并重发；这些行为不能替代 Drov 的审批、原计划和单次写入规则。SDK 的 AnyIO 上下文必须由同一个任务进入和退出，不能直接把上下文分别放在 Runtime 的启动/关闭任务中。

## 决定

- 可选 `drov[mcp]` 精确固定官方 mcp 2.3.0，沿已有锁使用 mcp-types 2.3.0/jsonschema 4.26.0；不复制 SDK 源码。`MCPStdio` 构造无 I/O；`discover()` 用临时真实进程完成初始化与有界分页发现并关闭，返回固定 MCPTool。随后 Agent/Runtime 使用该定义；owned/factory 资源在 Runtime.open 打开新进程并核对完整快照，borrowed 必须已打开。发现不是工具执行，预检不得调用业务工具。
- 使用 SDK 的 `mode="legacy"` initialize 握手（实际夹具协商 2025-11-25），关闭响应缓存；只启用 tools。不采用 auto 的现代发现/协议升级；仅支持 SDK 的 initialize 握手版本，不宣称覆盖 2026 协议。协商版本和完整发现清单进入 source_hash；Runtime 开始与每次调用前重新读取全部页，变化即拒绝，原审批不换 schema、不重新规划。服务自身的并发变化和实现行为无法由旧协议原子固定；应用仍需维护可信服务与 revision。
- 逻辑名为 `mcp.<namespace>.t_<远端名SHA256前32位>`，保留 `remote_name`。完整远端描述、输入/输出 schema 和 metadata 的快照、命令/参数/cwd/环境变量名/超时的配置摘要及应用 effect/approval 参与注册指纹；env 值不参与，修改凭据、服务实现或权限含义需新 revision。命令必须是宿主提供的绝对可执行文件路径，参数是列表，无 shell；默认只继承固定SDK的OS白名单（POSIX的HOME/LOGNAME/PATH/SHELL/TERM/USER，Windows的APPDATA/HOMEDRIVE/HOMEPATH/LOCALAPPDATA/PATH/PATHEXT/PROCESSOR_ARCHITECTURE/SYSTEMDRIVE/SYSTEMROOT/TEMP/USERNAME/USERPROFILE），env/inherit_env由宿主显式追加或覆盖；不继承任意宿主变量/模型Key。白名单策略版本进入source_hash。stderr 丢弃，不写事件或错误详情。
- 输入采用有界、闭合的 JSON Schema 子集：标量、enum、对象、数组、nullable union 和已有数值/长度约束；title 丢弃，未指定 additionalProperties 的对象收紧为 false。拒绝 `$ref/$defs`、开放字典、任意联合等不支持结构，不自动猜测转换。optional 字段缺省仍缺省，default 是注释，不改审批参数；按 JSON Schema 2020-12 校验（不做 Python 值强转，integer 遵循 JSON Schema 数学整数规则）。
- MCPTool 是 PythonTool 的专用子类型，使用同一个 dispatcher、Authorizer、PermissionPolicy、预算、操作 journal、持久审批及 unknown/reconciliation。annotations 不授予权限，默认 unknown/policy→ASK；只有宿主 MCPToolPolicy 显式分类。原始 ClientSession.call_tool 每次只发一条 tools/call，无适配层重试、input-required 自动回合或 header-mismatch 重发。工具错误、无效结果、断连、超时/取消的 write/unknown 保守记 unknown；read 为 failed_known；未授权/非法参数不调用。
- 只返回 text content 与有限 JSON object/null 的 structuredContent；SDK 校验服务声明的 outputSchema。图片/音频/资源/非对象结构结果拒绝，不自动抓取 URI、显示 HTML 或转换为 Artifact。task-required 工具在发现阶段拒绝；sampling/roots/elicitation 不提供回调，SDK 明确拒绝，resources/prompts、远程认证及任务扩展不暴露。
- 每个连接由专属 task 进入/退出 SDK session/stdio 上下文。调用异常/取消关闭整个连接，停止其它并发调用，不自动重连；实际清理未结束时调用/close task 继续持有引用。Runtime 关闭超期报 ShutdownIncomplete，保留存储锁，之后可再次 aclose 等待同一次清理。SDK 完成受测进程树退出后才释放；这不是 Docker 沙箱，不覆盖主动逃离进程树的后台守护程序。解析后的发现/工具结果限额不等于原始 stdout 的内存硬上限。

## 替代方案与代价

直接保存高层 Client 上下文最少代码，但跨任务退出 AnyIO scope 不成立，自动重发也破坏单层派发。把首次发现放进 Runtime.open 会使注册与 schema 在接收时尚不确定；因此采用显式短暂预检进程，代价是服务启动两次，stdio 启动程序本身应可安全预检。只信任首次发现更快，但不能发现待审批期间的元数据变化；每次重读清单增加本地 RPC 和延迟，不提供远端行为的事务性保证。自动转化任意 JSON Schema 会产生不可解释的模型/校验差异，因此首期明确拒绝不支持子集。

旧记录/DDL/公共 DTO/schema 不变。未配置 MCP 的 Agent 指纹保持原算法；新 MCP 定义加入其专属指纹字段。只有相同配置/协议/发现快照和工具策略可恢复原计划。凭据只由宿主传入，不保存在定义快照或数据库；应用仍不得把秘密放进命令参数或服务描述。

## 来源

查阅日期：2026-10-15。官方 [SDK 仓库](https://github.com/modelcontextprotocol/python-sdk)，固定 v2.3.0 提交 `2118f14f8a19bc158d8a1cf90af58d85d187f849`（GitHub tag API 实查，非 main）；已安装 wheel 的 Client/ClientSession/stdio 与进程树清理源码及 MIT 元数据实查。协议依据：[2025-11-25 tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 的分页/annotations/结果/schema，及 [生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。Drov 的预检、审批、映射与拒绝策略是本项目决定，不是 MCP 全协议能力声明。


Windows验收修正：最初把SDK的全部OS变量也清空，Windows真实进程初始化失败；已恢复固定SDK的OS白名单，同时维持不继承任意宿主变量。Python官方 [subprocess说明](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen) 要求Windows的显式env包含有效SystemRoot以加载side-by-side assembly（查阅2026-10-15）。原空环境方案不构成本卡最终契约；其他环境变量仍只能由应用显式提供。版本化环境策略使旧MCP指纹明确失配，不覆盖历史基线或默许恢复旧配置。

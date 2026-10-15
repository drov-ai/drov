# T-12 MCP stdio 验证

日期：2026-10-15（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`，main / d7dadca，工作区干净。已读 AGENTS/progress/导航/handoff，核实 implementation-plan 的 T-10B 前置及 T-11/T-10C 完整验收。GitHub 实查763c25d的核心38683462374/Docker38683462346均completed/success；不重新启动一般规划。

## 本轮交付与环境

实现 [ADR-0024](../decisions/0024-mcp-stdio-tools.md)、[MCPStdio/MCPToolPolicy](../../src/drov/integrations/mcp.py)、core-safe MCPTool、Runtime资源绑定/固定注册、同一dispatcher的可选字段处理、[官方SDK真实测试服务](../../examples/mcp_stdio_fixture.py)、[普通/stream示例](../../examples/mcp_stdio_offline.py) 与独立安装检查。源API/运行/架构/能力/工程/质量/跨入口案例/许可同步，不修改公共DTO/schema、SQLite DDL、品牌、历史P4指纹或原私有环境/云台账。

本机macOS ARM64，Python3.11.9与3.14.3，uv0.13.0。mcp2.3.0、mcp-types2.3.0、jsonschema4.26.0及其MIT元数据实际安装检查；只收紧已有mcp声明到精确2.3.0，解析版本/hash不变。GitHub tag API实查v2.3.0→2118f14f8a19bc158d8a1cf90af58d85d187f849；安装的client/client.py、client/session.py、client/stdio.py及POSIX/Windows进程清理五份源码与固定上游逐字节相等，记录`.drov/t12-upstream-{tag,source}.json`。协议来源及设计/协议边界见ADR，不用main当固定发布版本。

所有MCP连接均为本机独立stdio子进程；使用合成value7，无模型API、私有MCP服务、凭据读取或云调用。固定initialize握手实际2025-11-25，不能写成覆盖2026现代发现协议或整个MCP。

## 实际检查点

- 前置全库（启用本地Docker配置，不启用E2B云配置）：**1001 passed /61 skipped，214.82s**，`.drov/t12-prerequisite.log`。59项真实E2B因没有本轮授权配置跳过，另外两项为Python版本适用项；本轮没有读`.env.local`或动用旧云预算。
- 初步MCP矩阵55项通过，追加进程树/关闭锁/新连接审批恢复/策略预算/factory/取消后61项通过，最终补task-required/缺可执行文件/不匹配绑定后 **64 passed，46.86s**，`.drov/t12-mcp311.log`。后续最终检查点见下方，不能把中间结果冒充最终全库。
- 真实普通/stream示例各显示两页发现、审批前0/批准后1调用、两个进程实际退出；模型是ScriptedModel。跨新连接/SQLite重开审批保持原schema/参数与单次调用；工具错误、无效text/JSON结果、sampling请求、断连与卡住分别走read失败或write未知核查，原始错误正文不进入操作记录。
- 进程树样本包含忽略SIGTERM的父/子进程，真实SDK升级终止后用OS查询确认退出；关闭延迟屏障使Runtime明确ShutdownIncomplete，另一个SQLite实例仍StorageInUse，释放屏障后重试aclose并成功重开。包装Task取消不代替清理完成证据。
- Ruff、严格mypy与生成schema检查点通过；完整最终静态/构建/平台结果待追加。本轮记录与progress在远程矩阵结束前保持进行中，不提前关闭T-12。

主要实际入口（具体日志由末节补齐）：

```sh
uv sync --locked --extra openai --extra e2b --extra mcp
python -m pytest tests/test_mcp_stdio.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t12-dist311
python scripts/check_installed_mcp.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t12-dist311/drov-0.1.0a0-py3-none-any.whl
```

## 失败修正与边界

首轮测试夹具调用了不存在的journal.list，随后局部变量覆盖helper；按实际get接口修正。Ruff检查Markdown示例格式、严格类型检查SDK的snake_case参数/可选模块也已修正；这些失败没有被记成通过。初始化无tools时，原owner错误会使后续aclose重复报初始化错误；改为在owner内部记录初始化失败，让外层stdio正常清理，只把仍未结束/未确认的清理作为ShutdownIncomplete。未启动的缺可执行文件明确归为配置失败。另补打开完成前is_open不得为true。收尾审查改为原始session分页，避免高层Client重复吸收分页导致输出schema隐式重新发现；结果codec改用不可变JSON模板重建，防止共享字典使变更校验失效，并补缺extra在进程启动前明确提示。最终专项增加到66项，以下方最终实际结果为准。

发现/运行使用两个独立进程，服务启动本身必须可安全预检。关闭故障连接也会中断同连接其他调用，不自动重连。旧协议中列工具与调用不是原子事务；每次重读防止观察到的schema变化，不冻结远端实现。输入schema仅支持API列明子集，structuredContent仅有限JSON对象/null，媒体与资源拒绝。stdout由官方SDK解析，分页/结果字节限制是解析后限制；不声称恶意服务的硬内存或守护进程沙箱隔离。borrowed正常关闭由宿主负责，但调用故障会中止连接。真实模型质量、HTTP/UI、公开发布/部署均未做。


最终负载检查点：两套全库与两套打包/安装并发时，3.11有两项MCP测试因测试配置1s启动期限过短，在discover/open阶段超时（不是预期RPC错误/坏输出路径），其余1065通过、61skip，`.drov/t12-accepted-full311.log`。修正为非超时案例采用产品默认10s，专门hang案例5s；发现错误案例不再允许TimeoutError冒充目标错误，超时案例额外检查真实start/list屏障。SDK运行代码/默认值不改。该失败检查点不能写成最终全库通过；修正后结果见后续记录。

# T-11：OpenAI Responses 离线协议适配

日期：2026-10-15（Asia/Shanghai）。Local `/Users/yanlong/projects/drov`，main / aeea557 起点、工作区干净且与 origin/main 同步。先读 AGENTS/progress/README/handoff、T-11 前置/验收、API/工程/运行/质量及当前源码。前置 T-10B 的最终源码9a4fba1与核心38600296733/Docker38600296414均为success；本次未从基础探针推断整卡完成，也未重开P4。源码决定见 [ADR-0022](../decisions/0022-openai-responses-adapter.md)，任务状态只在 [progress](../planning/progress.md)。

## 实际范围

- 官方 openai 3.29.0 / httpx2 2.13.1 已按原 uv.lock 实际安装；声明最低版本与锁相同，没有编造其他版本兼容。核心/可选导入分离、构造无I/O，显式模型/能力/输出上限、惰性owned或borrowed客户端、生命周期关闭。
- function 工具逻辑名→固定wire名→完整响应还原，sandbox.run_python/严格参数/默认值/最终JSON schema均沿Drov协议。运行时权限/审批/工具派发仍归Runtime。
- 私有版本bundle保留原始output/加密reasoning/phase/顺序；完整助手组一一配对，在原位置还原，随后接原call_id工具结果；不使用远程会话。B-15包含实际大provider项共同预算/完整旧轮次排除，原持久响应不改写，inspection不显示协议明文。
- SSE/非流响应共用完整归一，唯一终止且EOF之后才complete；未知/乱序/重复/截断/畸形/拒绝不产生可执行计划。报告token按原值，缺值保持None、不估价。HTTP/连接/超时错误分类和正文脱敏，SDK重试0、Runtime单层重试，Retry-After秒/ms/date受剩余预算限制。
- Agent可选校验/公开配置指纹hook，OpenAI模型ID/输出上限入指纹；旧适配器不增加该键。公共DTO/schema、SQLite DDL/扩展、uv.lock、原P4指纹和品牌均未改动。
- 新增本地HTTP示例、仓库外core+extra安装验证、CI普通核心与extra分开验证、Docker专项附加Responses→真实执行。未创建MCP/HTTP/UI/E2B空壳，没有付费模型/云调用、密钥读取、发布或部署。

## 证据与检查

环境：本机 macOS ARM64、Python 3.11.9 与独立 `.drov/check-py314` 的3.14.3；uv使用原 `.drov/tooling/bin/uv` 0.13.0，不修改全局工具。Docker沿用T-10B已配置的Desktop Linux ARM64 VM与固定标准库镜像，不拉取/构建新镜像。

| 检查 | 实际结果 / 证据 |
| --- | --- |
| 前置全库含真实Docker | 895 passed /2 skipped，210.88s；`.drov/t11-baseline.log`，仅Python3.12+ API不适用 |
| 初批HTTP/SSE | 44 passed；扩充后57项，再加phase/大协议预算专项；不使用HTTP MockTransport代替本地连接 |
| 首次综合专项 | 修正夹具后59 passed /3.66s，包含2项实际Docker；`.drov/t11-adapter-final2.log`；此后新增phase/预算/缺extra，最终结果待下方追加 |
| 公开协议/extra缺失专项 | 68 passed /1.16s，`.drov/t11-protocol-final.log` |
| 静态/schema检查点 | Ruff180 files、mypy115 files、schema check通过；无extra的独立3.14 mypy亦已通过，最终再次核对 |
| 本地示例 | SDK/stream各实际2次loopback请求，工具返回36、Report(total=36)，real_model=false；无外部模型 |
| 最终全库3.11含真实Docker | **957 passed /2 skipped，214.91s**；`.drov/t11-full311.log` |
| 最终全库3.14含真实Docker | **959 passed，228.06s**；`.drov/t11-full314.log` |
| 两解释器构件/核心安装 | wheel/sdist及重建逐文件比对，4套core-only安装、六示例各SDK/stream通过；`.drov/t11-package311.log` /314.log |
| extra干净安装 | 2套仓库外非editable wheel+openai 3.29.0，各SDK/stream2次loopback请求、Report36；`.drov/t11-installed-openai311.log` /314.log |
| 最终静态/文档 | Ruff181 files、mypy115 files、schema、Twine strict、66文档/540相对链接/14 AST错误0；git diff --check通过 |

主要命令：

```sh
uv sync --locked --extra openai
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" .venv/bin/python -m pytest
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" .drov/check-py314/bin/python -m pytest
.venv/bin/python scripts/check_package.py --uv .drov/tooling/bin/uv --out-dir .drov/t11-dist311
.drov/check-py314/bin/python scripts/check_package.py --uv .drov/tooling/bin/uv --out-dir .drov/t11-dist314
```

## 失败、修正与限制

首批44项中2个失败是测试误用events异步订阅API和用核心已拒绝的动态dict参数构造工具；改为真实inspect读取及适配器独立schema拒绝断言。扩充测试也曾误用respond/resume参数，按既有Runtime入口修正，未修改生产审批规则。一次未导入ModelDelta使测试在provisional delta后退出，触发未关闭HTTP资源警告；修复夹具后显式生成器关闭/取消/超时均观察到服务端断连，警告不忽略。

真实Docker首轮2项因夹具手写JSON转义的代码字符串产生SyntaxError，实际exit=1且没有ready产物，框架正确保留失败。改成json.dumps构造相同预定代码后，实际exit=0、CSV字节/hash、确认销毁和同适配器后续请求全部通过；不是SDK或隔离实现修复。聊天中曾提前称该轮通过，已在同轮纠正，以日志为准。

所有fixture凭据是显式合成字符串，官方客户端只访问127.0.0.1随机端口、trust_env=False。没有读取宿主OPENAI_API_KEY或.env；没有真实模型/服务/预算。成功只证明受测官方SDK版本/本地协议与Drov机制，不证明任何真实模型ID可用、reasoning模型质量、其他compatible服务支持或零保留政策。已报告的费用仍未知；Limits上下文预留与供应商输出上限需由应用协调。

下一项T-12前不需要新仓库/账号/Key/model ID/预算；离线stdio服务夹具由执行者准备。T-16授权smoke前才确认服务、可访问固定模型ID、能力依据与费用上限；凭据由用户在本地环境或借用客户端安全配置，不粘贴聊天或写入Agent/model_config。会话建议和最终Git/远程状态统一见progress。

## 最终本地验收与远程核对

新增 **62**：OpenAI HTTP/SSE/Runtime/BackendFixture/phase/B-15共 **59**、缺extra核心测试 **1**、实际Docker **2**。全库 **959**；真实Docker组由97增为99，3.11仍仅两个Python3.12+ API不适用。默认无extra核心CI会明确跳过两个适配文件，不把skip当专项通过；extra阶段实际执行59项HTTP/Runtime，Docker工作流实际执行99项。最终本地全库均无新增失败/警告。

两解释器构件一致：wheel `191af16bcb9f29c72f521b61f66528b3477ea8ffcf3356a0068e13a168ce6c12`，sdist `7592b70686ca6a4f68fa8f3c77cc87ebbf5b3201b20e952f5b9257d6b874b73d`。wheel全部63项drov文件逐字节等于最终src；构件在最终验证/进度补记前产生，sdist不冒充后续文档相同hash。新增extra安装脚本首次用相对uv路径切换临时目录失败，改为绝对路径后两套实际安装通过；这项检查脚本修正和最终文档在旧sdist之后，SDK wheel内容没有变化。最终远程使用新提交重新构建/安装。

本地整卡范围已通过；最终源码提交/远程CI结果核对后追加，不把前置9a4fba1的CI视为本卡证据。没有真实模型smoke/质量、其他SDK/compatible服务/E2B/MCP/HTTP/工作台/维护验收；首版发布条件仍按原任务链。原SQLite/bytes/阶段记录及Docker配置保留，不清空生产或未知租约。

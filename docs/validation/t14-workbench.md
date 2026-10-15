# T-14 本地工作台验证

日期：2026-10-15（Asia/Shanghai）。起点 `/Users/yanlong/projects/drov`，main / cf6c8cd；工作区干净，与 origin/main 同步。已读 AGENTS/progress/导航/handoff、T-14任务卡、API/运行/工程/质量及ADR-0025。实际查询前置26b97ee的核心38746896113与Docker38746896088，均completed/success、head_sha相符，分别10/4个job；元数据存于`.drov/t14-prerequisite-{core,docker}.json`。T-13无剩余验收缺项，没有重开一般规划。

## 实际交付与边界

按 [ADR-0026](../decisions/0026-local-workbench.md) 实现同源工作台、固定公开登录资产/认证数据、最小注册目录、会话/Run/上下文/事件、审批/输入/核查/取消/恢复及认证fetch重连。受控上传仍由原接收事务消费，文件经授权/hash读取，真实PNG才解码，其他文件仅附件。TypeScript类型来自真实OpenAPI，构建资源纳入统一wheel/sdist；core-only无需Node/Web导入。原Runtime/DTO/DDL/uv.lock/P4指纹和忽略品牌原素材不变；HTTP新增AgentCatalog、无损会话查询入口和DrovApp.workbench装配选项，源契约/案例/质量/归属同轮同步。

本机macOS ARM64，Python3.11.9/3.14.3、uv0.13.0、Node24.14.0/npm11.9.0。实际精确npm锁：Vite8.3.4、TypeScript5.9.3、openapi-typescript7.13.0、Playwright1.64.1、Prettier3.8.3；浏览器Chromium156.0.8078.4（Playwright build1248）。没有读取真实Key/私有env、付费模型或云调用，没有公开发布包/镜像/站点或部署。测试文件、身份、模型输出全部合成；真实Docker复用已准备的本机Engine/profile，不拉取/构建新镜像。

## 失败、修正和中间检查点

- npm首次解析TypeScript7.0.2被openapi-typescript7.13.0的peer `^5.x`拒绝；改为实际兼容的5.9.3，未使用force/legacy-peer。Prettier3.9.6两次下载遭ECONNRESET，最终从本机缓存安装并锁定3.8.3；不把失败下载当已安装。
- 初次浏览器fixture在macOS的符号链接临时祖先目录被LocalArtifacts正确拒绝，改为解析真实路径；不放宽生产字节层。其后回归恢复fixture最初注入的失败未进入重启扫描，不能期待当前运行自动recover；改为在已有final保存后显式形成recovery检查点，页面只验显式恢复/原结果不重调。真实硬崩溃矩阵仍由原T-10/T-10B覆盖。
- 首轮可用浏览器矩阵8通过/4失败/1无Docker配置skip（`.drov/t14-ui-second.log`）。修正开始后重复loadRun导致订阅被自身取消、撤权开关/错误信息未同步、文件描述以user消息追加使脚本例误判断当前阶段。三参考应用、写入/拒绝/核查不变；没有改内核来迁就UI。
- 启用真实Docker后的初轮12通过/1失败，发现暂存后重建表单把已选择Agent重置为第一项，原服务正确拒绝跨目标消费。保留选中值后，真实上传/审批/执行/结果已成功，但自动测试对异步按钮移除的while循环产生竞态；改为逐文件等待真实信息响应，未延长超时掩盖失败。日志`.drov/t14-ui-third.log`、fourth.log保留。
- 修正后13项浏览器全通过，含真实Docker（11.9s，`.drov/t14-ui-fifth.log`）：实际上传numbers.csv、批准原代码、生成25字节summary.csv与120×80 PNG、发布后active lease=0、CSV实际内容/hash、PNG解码/预览/下载，查看前后模型调用计数不变。桌面/390px视口截图实际已打开检查，无横向溢出。后续补3项协议/字节检查，无Docker的16项运行15通过/1明确skip（9.6s，`.drov/t14-ui-sixth.log`），不把这次skip冒充Docker成功。
- Python原HTTP62项通过（3.98s，`.drov/t14-http-first.log`）；新增5项公开资源/认证边界与原HTTP合计67通过（4.62s，`.drov/t14-http-static.log`）。本地真实HTTP资产探针通过（`.drov/t14-local-assets.log`）。Ruff、228文件格式、155文件严格mypy、schema/OpenAPI检查已通过；最终构件/CI另记，不以中间结果关闭整卡。

补充接口审查发现合法会话ID可含斜杠/点段，原路径入口不能完整表示；新增查询形式与原读取共用同一函数/授权，不改SDK标识范围。新增5项先得到实际404失败（`.drov/t14-session-before.log`），接通后原HTTP+资源/查询合计**72通过（5.02s）**，`.drov/t14-http-query311.log`。页面改用查询形式，并补端到端往返。

## 验证入口

```sh
npm --prefix workbench ci
npm --prefix workbench run schema
npm --prefix workbench run format:check
npm --prefix workbench run build
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" npm --prefix workbench test
python -m pytest tests/test_workbench.py tests/test_http.py tests/test_http_transport.py tests/test_http_artifacts.py tests/test_http_scenarios.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t14-dist311
python scripts/check_installed_server.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t14-dist311/drov-0.1.0a0-py3-none-any.whl
```

## 验收范围及未验证

工作台为实验性本地开发界面。浏览器只验Chromium及移动尺寸视口，不宣称Firefox/WebKit、真实手机/读屏器或外部用户体验通过；不改变Python既有支持矩阵。原单worker/Host-Origin/Bearer、POSIX文件层、已知长度原始上传和T-15暂存维护限制保留。没有E2B跨HTTP/UI声明，未动用其历史云预算。真实模型质量和费用仍留T-16；独立业务副作用恢复/硬崩溃的原矩阵没有以UI合成暂停替代。

正文默认关闭；空闲页面不即时感知宿主撤权，下一次同步/读取会清除正文/文件名/Blob并丢弃旧视图响应；已经下载的字节无法撤回。UI只展示最近1000个事件、暂时输出有界，完整日志留在原存储；它不是持久导出、离线维护或Notebook。代码结果未发布时，UI只说明尚无已发布销毁证据，不推断已清理。品牌选定SVG为逐字本地副本，其他品牌原素材未改；初次公开包/站点前仍需来源/公开使用范围整理。

## 最终结果

最终代码检查点（提交/CI另补）：**23项Playwright全部通过（17.6s）**，其中4项协议/字节、19项浏览器流程，真实Docker必跑（DROV_REQUIRE_DOCKER=1），`.drov/t14-ui-complete311.log`。Docker用例还在已预览后撤销view_content，后续下载拒绝并清除文件名/图片；核查/取消/拒绝/查看均有真实计数断言。补测输入草稿/焦点在同Run补读时保留，第一次测试误把形成暂停所需的SSE转移也阻住而超时，改为先等待实际暂停后触发补读（原日志`.drov/t14-ui-accepted311.log`），没有改内核或延长期限。

Python历史全库检查点（查询入口前）3.11为**1137通过/61skip，305.86s**（`.drov/t14-full311.log`）；不得把该结果当新增查询入口的整库结果。新增5项查询和原HTTP/资源已**72通过，5.02s**，最终全库/构件/平台另补。当前状态与未完成项统一见 [progress](../planning/progress.md)。

# T-17 文档、社区与官网构建验证

日期：2026-10-17（Asia/Shanghai）。任务状态唯一维护在 [progress](../planning/progress.md)。本记录区分本地构件、支持平台 CI 和公开托管；本地站点可运行不等于已上线。

## 起点与范围

实际 checkout `/Users/yanlong/projects/drov`，main / `fb916d6331ba778770f03eec06607e3332002956`，起点工作区干净且与 origin/main 同步。重读 AGENTS、progress、规划导航和 handoff，核实 T-17 的 T-10B/T-14/T-15 依赖以及 T-16 整卡完成。实际重新读取 GitHub：T-16 最终核心38942872052为11 jobs、Docker38942872230为4 jobs，均精确绑定 `0aa0168572f7994326e84ab66e53a0a83fae6132` 且 completed/success；没有跳过上项缺项。

本轮交付 [README](../../README.md)、[用户文档](../guide/index.md)、[CONTRIBUTING](../../CONTRIBUTING.md)、[SECURITY](../../SECURITY.md)、issue/PR 模板、MkDocs 配置/自有主题、[构建器](../../scripts/build_docs.py)及[输出检查器](../../scripts/check_docs.py)。新增 docs 依赖组，实际解析11个新增文档依赖（含MkDocs1.6.1）；原锁中已有包的版本逐项相同，没有升级运行时依赖。更新工程方案、开发/例子/Docker 说明中的旧状态表述，不修改 SDK、schema、SQL、模型评测或工作台/品牌原字节。

按用户本轮明确回复启用 `drov-ai/drov` 的 GitHub 私密漏洞报告。原查询 enabled=false，PUT成功后重新GET为true，安全政策使用实际私密入口；没有编造维护者邮箱或发送漏洞/外部消息。[GitHub 官方机制](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)查阅2026-10-17。

## 本地实际验证

| 对象 | 方法与结果 |
| --- | --- |
| MkDocs严格构建 | `uv run --locked --group docs python scripts/build_docs.py`；21个HTML页面、778个本地链接/锚点、29个文件；版本路径0.1.0a0、源码链接固定commit、无canonical假域名 |
| 来源与收录边界 | 只复制显式允许的指南/社区/schema；不带规划目录、评测原始JSON、私有配置/台账或品牌。Lunr/MkDocs未使用的搜索JS移除，仅自有客户端与本地JSON索引；第三方构建依赖来源在THIRD_PARTY/锁中 |
| 拒绝路径 | 输出副本中分别注入根私有ledger、版本内非schema JSON、.env.local、缺失锚点、越界链接、重复ID，六项均拒绝；夹具在临时目录，不操作用户库 |
| 文档检查 | 最终118份Markdown/1008个本地文件链接/21个Python片段AST检查通过；语法结果不代替下列可执行示例 |
| 3.11.9构件/核心隔离安装 | `scripts/check_package.py`实际wheel/sdist/源码重建，各两个新core-only环境；pip check、导入无可选模块/副作用、11个核心例子各普通/stream共44次执行，包含三参考应用、维护/备份/迁移/导出与文件输入 |
| 3.14.3独立安装 | 新venv安装wheel及server/mcp/openai；从仓库外执行快速入门完整代码，输出Report(total=30)；HTTP普通/SSE、MCP普通/stream、OpenAI与DeepSeek方言各普通/stream共8条实际本地客户端/进程路径通过 |
| 3.14.3真实Docker | `scripts/check_installed_sandbox.py`从另一个新core-only环境，标准库/分析镜像×SDK/stream四项通过；实际60/summary.csv、分析PNG、销毁后重开授权读取/hash通过，不重跑旧目录 |
| sdist文档可构建 | 新构件解压到独立目录，uv锁定安装docs组/Python3.14.3后实际构建，同21页/778链接通过；无Git时明确source_archive和SDK基准commit，不伪装成正式发行Tag |
| 静态与相关回归 | 最终Ruff检查/格式312文件、mypy194源、schema/OpenAPI检查通过；package/model_protocol/schema_snapshot相关11项通过；另显式`uv sync --locked`移除docs组并断言MkDocs不存在，mypy仍通过，确保文档工具未变成必装依赖 |
| 浏览器 | 本地IAB真实打开首页→入门；中文“恢复屏障”7条搜索与锚点跳转；子路径/docs/下搜索目标保持前缀；390×844和1280×900检查，最终scrollWidth等于clientWidth，无页面横向溢出；恢复默认viewport |

Docker只使用起点既有的可信CLI/Unix socket、标准库manifest及分析image ID `sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45`。不拉取/重建镜像、不操作共享daemon或历史资源；原T-10B深度故障证据不混称为本轮重跑全部专项。

## 发现与修正

- 首次快速入门真实执行发现 `@tool` 缺少必填name，报TypeError；同轮修正教程/API导航，再由独立wheel环境实际执行通过，没有更改SDK来迁就文档。
- 首次产物检查拒绝MkDocs生成的空sitemap；收录其确定文件名/类型并规范gzip时间，保留严格范围校验。
- 移动端发现多列导航、长扩展名正文溢出与CSS覆盖hidden；改为两列grid、允许正文断行并确保hidden优先，重新验证搜索隐藏/导航与页面宽度。
- 生成签名起初未符合Markdown内Python格式；现直接从实际源码提取签名和装饰器，正常构建核对，不手工维护第二套参数。

这些失败都在验收前发现并保留说明；不将预设模型例子记入真实模型质量。本轮无任何付费模型、E2B/其他云执行、站点上传、域名绑定、PyPI/TestPyPI或GitHub Release发布。

## 构件与恢复

本地静态输出 `.drov/site`、文档zip/hash在dist，manifest绑定版本/提交/dirty标记和实际输入hash；已验证SDK原数据、私有环境、历史SQLite/bytes/租约与云台账均未用作夹具。站点不复制忽略的品牌素材，原件保持不变。源码/生成schema/主题/社区文件纳入sdist，wheel仍只包含原SDK资产与必要元数据。

本地原日志保存为 `.drov/t17-{build,package,installed-examples,docker,sdist-docs,site-boundaries,docs-source-check,regression,core-only-types}.log`；截图为`.drov/t17-home.jpg`与`t17-mobile.jpg`。浏览器恢复默认viewport并关闭本轮临时页，两个自有loopback预览进程已停止；zip与本地源/截图保留，用户可按托管说明重新启动预览。

最终实现检查点为 `d35f2123c8a5a11eb12d08f64bc678c62d932ae0`（40f315e首批、5aab10c显式UTF-8、d35f212区分无Git来源与提供显式签名重生成）。无Git包的source_commit为null，单列SDK链接基准，不伪造整个源码包的Git来源。最终源码在干净工作区普通构建与`--update-signatures`显式重生成后构建的zip摘要相同；后者没有产生签名差异。91个wheel内SDK文件与冻结源码逐字相同；wheel依赖元数据没有MkDocs。最终sdist另行解压，用已锁定docs组/Python3.14实际构建并检查source_archive/null来源，两种源码模式均通过。

[本地结构化证据](t17-local-evidence.json)保存实际构件路径、摘要、环境、成功/拒绝计数与原日志hash。文档zip为`dist/drov-docs-0.1.0a0.zip`，SHA256=`dfa4e1067ca242d6b842d5e042cb9315c139d1768ec0e104decf8492a5b65e9f`；21页/778站内链接/29文件。同源码wheel/sdist分别为`9b431f5279093cadefd0c6050e482d4e691740d9a367e7fc0dcac23b995986db`与`faf9ec0750b56c2c2991e1a0ac07d04f703df253e27bafe5f88284133c694462`，位于`.drov/t17-release-checkpoint-dist`，不是已发布包或T-18的最终RC。T-18应重新冻结包含收尾材料的候选，而非误用较早dist摘要。

## 任务卡逐项对应

| T-17检查对象 | 对应交付与实证 |
| --- | --- |
| 快速入门/API/部署/限制/排错 | [指南导航](../guide/index.md)覆盖全部入口；可复制完整程序在新wheel环境实际执行，签名从源码提取并校验；服务教程使用可运行本地HTTP/工作台，不虚构线上部署 |
| Docker/文件/容量/备份/威胁边界 | [沙箱](../guide/sandbox.md)与[维护](../guide/maintenance.md)引用唯一源限额/契约，四条标准库/分析/SDK/stream新安装真实执行；历史深度隔离证据单独链接 |
| 可选E2B准备/费用/支持表 | [支持表](../guide/limits.md)与沙箱指南单列真实T-10C边界、凭据引用/上传授权/独立预算、区域声明限制，不调用云 |
| README/社区/安全模板 | 源码入门、贡献/安全政策、两类issue与PR模板实际入库；私密渠道按用户选择启用并GET复核，无虚构邮箱 |
| MkDocs首页/版本/Pages产物 | 0.1.0a0路径/版本页/自有主题、严格构建与链接/收录拒绝检查；可预览zip/hash/manifest，同输入两次字节相同；无Git来源单独标记 |
| 链接/许可/真实状态 | 本地文件链接与站点锚点检查；Apache自有内容与上游/品牌边界清楚；无已发布/已部署/任意任务可靠性/竞品领先声明 |

## 最终精确提交与远程验收

`d35f2123c8a5a11eb12d08f64bc678c62d932ae0` 已推送 origin/main。其[核心38991997845](https://github.com/drov-ai/drov/actions/runs/38991997845) **13 jobs** 和[Docker38991997762](https://github.com/drov-ai/drov/actions/runs/38991997762) **4 jobs**全部completed/success；逐项核对headSha、jobs和steps，没有失败步骤。完整平台/步骤/原日志hash见[最终CI快照](t17-final-ci.json)，不把40f315e/5aab10c被后续提交替代或取消的运行写成最终成功。

13项包含两套新增文档检查（Linux3.11/Windows3.14）、八套既有支持平台的适用全库/构件/核心及extra安装、质量、工作台浏览器与Python3.15探测；3.15通过仍仅为探测，不升级正式支持。四套Linux Docker包括固定分析镜像、实际故障/文件/硬终止、core-only安装后SDK/stream，3.11另含实际Docker工作台。普通核心缺Docker/云配置的skip不等于对应执行通过，云未新增调用。

任务卡内本地交付、干净运行、链接/许可/真实状态及站点构件均已对应验证。精确源码CI通过后，收尾只改progress、本记录与两份证据JSON，以skip ci保存；SDK、源码工具/指南/主题/锁及本地zip保持已验证字节，不用文档补记伪称重建成同一个sdist。任务关闭与下一项统一见progress。公开托管和发包仍按下列限制处理。

未验证：Cloudflare Pages远端构建/部署、最终域名/DNS与品牌公开授权、PyPI/TestPyPI项目控制权/OIDC、正式RC/Tag/公开安装。它们按发布策略在T-18处理；无需新模型ID、Key或追加预算，旧模型批次不重跑。独立用户体验仍未开展，不声称易用性/竞品领先。

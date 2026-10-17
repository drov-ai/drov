# T-17 文档、社区与官网构建验证

日期：2026-10-17（Asia/Shanghai）。任务状态唯一维护在 [progress](../planning/progress.md)。本记录区分本地构件、支持平台 CI 和公开托管；本地站点可运行不等于已上线。

## 起点与范围

实际 checkout `/Users/yanlong/projects/drov`，main / `fb916d6331ba778770f03eec06607e3332002956`，起点工作区干净且与 origin/main 同步。重读 AGENTS、progress、规划导航和 handoff，核实 T-17 的 T-10B/T-14/T-15 依赖以及 T-16 整卡完成。实际重新读取 GitHub：T-16 最终核心38942872052为11 jobs、Docker38942872230为4 jobs，均精确绑定 `0aa0168572f7994326e84ab66e53a0a83fae6132` 且 completed/success；没有跳过上项缺项。

本轮交付 [README](../../README.md)、[用户文档](../guide/index.md)、[CONTRIBUTING](../../CONTRIBUTING.md)、[SECURITY](../../SECURITY.md)、issue/PR 模板、MkDocs 配置/自有主题、[构建器](../../scripts/build_docs.py)及[输出检查器](../../scripts/check_docs.py)。新增 docs 依赖组，实际解析 MkDocs1.6.1及其11个新增包；原锁中已有包的版本逐项相同，没有升级运行时依赖。更新工程方案、开发/例子/Docker 说明中的旧状态表述，不修改 SDK、schema、SQL、模型评测或工作台/品牌原字节。

按用户本轮明确回复启用 `drov-ai/drov` 的 GitHub 私密漏洞报告。原查询 enabled=false，PUT成功后重新GET为true，安全政策使用实际私密入口；没有编造维护者邮箱或发送漏洞/外部消息。[GitHub 官方机制](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)查阅2026-10-17。

## 本地实际验证

| 对象 | 方法与结果 |
| --- | --- |
| MkDocs严格构建 | `uv run --locked --group docs python scripts/build_docs.py`；21个HTML页面、778个本地链接/锚点、29个文件；版本路径0.1.0a0、源码链接固定commit、无canonical假域名 |
| 来源与收录边界 | 只复制显式允许的指南/社区/schema；不带规划目录、评测原始JSON、私有配置/台账或品牌。Lunr/MkDocs未使用的搜索JS移除，仅自有客户端与本地JSON索引；第三方构建依赖来源在THIRD_PARTY/锁中 |
| 拒绝路径 | 输出副本中分别注入根私有ledger、版本内非schema JSON、.env.local、缺失锚点、越界链接、重复ID，六项均拒绝；夹具在临时目录，不操作用户库 |
| 文档检查 | 本地Markdown文件链接与Python片段AST检查；最终数量在收尾补记。语法结果不代替下列可执行示例 |
| 3.11.9构件/核心隔离安装 | `scripts/check_package.py`实际wheel/sdist/源码重建，各两个新core-only环境；pip check、导入无可选模块/副作用、11个核心例子各普通/stream共44次执行，包含三参考应用、维护/备份/迁移/导出与文件输入 |
| 3.14.3独立安装 | 新venv安装wheel及server/mcp/openai；从仓库外执行快速入门完整代码，输出Report(total=30)；HTTP普通/SSE、MCP普通/stream、OpenAI与DeepSeek方言各普通/stream共8条实际本地客户端/进程路径通过 |
| 3.14.3真实Docker | `scripts/check_installed_sandbox.py`从另一个新core-only环境，标准库/分析镜像×SDK/stream四项通过；实际60/summary.csv、分析PNG、销毁后重开授权读取/hash通过，不重跑旧目录 |
| sdist文档可构建 | 新构件解压到独立目录，uv锁定安装docs组/Python3.14.3后实际构建，同21页/778链接通过；无Git时明确source_archive和SDK基准commit，不伪装成正式发行Tag |
| 静态与相关回归 | Ruff检查/格式311文件、mypy194源、schema/OpenAPI检查通过；package/model_protocol/schema_snapshot相关11项通过；另在不安装docs组的核心开发环境运行mypy，确保文档工具未变成必装依赖 |
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

本地原日志保存为 `.drov/t17-{build,package,installed-examples,docker,sdist-docs,site-boundaries,docs-source-check,regression,core-only-types}.log`；截图为`.drov/t17-home.jpg`与`t17-mobile.jpg`。精确提交/最终CI、构件摘要和日志hash在完成后补记。

未验证：Cloudflare Pages远端构建/部署、最终域名/DNS与品牌公开授权、PyPI/TestPyPI项目控制权/OIDC、正式RC/Tag/公开安装。它们按发布策略在T-18处理；无需新模型ID、Key或追加预算，旧模型批次不重跑。独立用户体验仍未开展，不声称易用性/竞品领先。

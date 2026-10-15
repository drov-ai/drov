# Drov 本地工作台

实验性 TypeScript/Vite 界面，使用 DrovApp 的同源 HTTP/SSE。Python wheel 已包含构建后的页面，使用者无需 Node。没有独立执行循环或浏览器直连模型/沙箱。

## 本地使用

在已按仓库锁安装 server extra 的环境中，从仓库根目录运行：

```sh
python -m examples.workbench_offline
```

打开终端显示的本地地址，将**本次启动的本地访问令牌**粘贴到登录表单；它不是模型 Key，不要把任何模型/云 Key 填入该表单。示例仅在本机显示这枚临时 UI 令牌，不将其写盘/加入 URL；DrovApp 本身不自动打印令牌。刷新页面后重新登录，断开页面不会取消运行。实际服务可在 DrovApp lifespan 内由宿主取得 `local_token`，或接入自己的 AuthAdapter。

样例提供研究检索、事务 Memory、澄清→审批→单次合成写入。默认元数据视图；打开「显示正文」才读取输出/上下文/代码/文件名。`research` 的任务 `fail` 显示失败；`slow` 可检查取消和重连；`operations` 的任务 `unknown` 走原核查器与人工核查。以上全是确定性脚本模型，不读取真实 Key、不发出模型调用。

显式启用本 checkout 原有本地 Docker/分析镜像（不拉取、不构建、不调用云）：

```sh
python -m examples.workbench_offline --docker-config .drov/sandbox-environment.json
```

选择 csv-sandbox，上传名为 numbers.csv 的合成输入（列 value，三行 12、18、30），提交任务，检查原代码/输入后批准；实际生成 summary.csv 和 chart.png。显示的销毁依据来自已发布 sandbox 结果。示例是固定输入计划，不能当作任意 CSV 分析或模型智能。每次运行建立独立沙箱；复用文件必须由宿主显式授予。LocalArtifacts 的 POSIX 限制沿原实现，Windows 工作台仍可使用不带本地文件存储的服务。

普通应用装配 DrovApp 后访问 `/workbench/`；设置 `workbench=False` 可关闭该界面，HTTP 不受影响。目录只展示当前服务已注册 name/revision，启动仍接受原权限检查。身份系统/TLS/账号轮换由宿主提供，不在浏览器配置后端、镜像或凭据。

## 开发与验证

Node 24.14.0 / npm 11.9.0，独立精确 package-lock；已验证组合为 Vite 8.3.4、TypeScript 5.9.3、openapi-typescript 7.13.0、Playwright 1.64.1、Prettier 3.8.3。工具全部为开发依赖。前端 schema.d.ts 从本仓库实际 OpenAPI 生成。

```sh
cd workbench
npm ci
npm run schema
npm run format:check
npm run build
npx playwright install chromium
npm test
```

浏览器夹具由仓库的 server/dev 环境运行；`DROV_TEST_PYTHON` 可指定另一套已安装依赖的绝对解释器路径。没有 Docker 配置时仅真实 Docker 用例明确跳过；需要该项实际执行时，在运行 npm test 的进程显式配置 `DROV_DOCKER_TEST_CONFIG` 指向本 checkout 的非秘密 profile。CI 的 Docker job 使用其本机 profile，不复用开发机路径。fixture 控制路由只在 tests/workbench_server.py 中，不进入 DrovApp。

构建直接输出到 `src/drov/server/static`；提交源码、锁、生成类型和对应资源，CI 重新生成并比较。Python 打包无需前端构建，sdist 含源码/锁和同一预构建资源。仓库外安装验证见 scripts/check_installed_server.py / check_workbench_assets.py。日志/截图仅生成在忽略目录 test-results，不能提交包含私有数据的截图。

## 展示与安全边界

具体浏览器限额唯一维护在 [工程方案](../docs/planning/engineering.md)。使用同源请求头认证和严格 CSP；不使用 cookie/localStorage/sessionStorage、外部字体/统计。正文全为转义文本；provider raw 项不呈现。下载检查原权限、大小/hash，PNG 还检查签名/像素/解码，其他文件仅附件。撤权在下一次同步/操作时清除已有视图和 Blob URL；已经下载的字节无法撤回。

网络丢应答/5xx 后「重试同一提交」复用原 request_key/body；响应重试复用原请求与响应内容，不换键重新执行。暂存文件未消费/失败/过期仍占原容量，离开页面不自动删除或回收，维护归 T-15。事件最多展示最近 1000 项；完整事实仍在原持久日志，UI 不是完整导出工具。页面查看不能恢复 Python 进程；没有持久 Notebook/多 Agent/Skill 或云沙箱选择器。

## 品牌和第三方来源

`public/drov-icon.svg` 为本仓库忽略目录 `drov-brand/svg/drov-icon-black.svg` 的逐字副本（用户已选定概念的矢量重构）；原素材不改、不把其余素材带入发行包。公开品牌来源/使用范围仍在首发前按 release-policy 核对，本轮未公开发布包或站点。没有复制其他产品 UI 或第三方图标库。

Vite 自动生成 module-preload helper 的 MIT 许可保存在 public/THIRD_PARTY.txt，并随静态资源进入 wheel。其他工具的实际版本/许可保留于锁与上游包：TypeScript/Playwright 为 Apache-2.0，openapi-typescript/Prettier 为 MIT。当前浏览器应用不导入这些工具的客户端 SDK。总归属见仓库 THIRD_PARTY.md；不把工具能力描述为 Drov 原创。

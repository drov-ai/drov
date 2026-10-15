# ADR-0026：同源本地工作台与静态构件

日期：2026-10-15。状态：工作决定；实际验收状态见 [progress](../planning/progress.md)。

## 原因与决定

T-14 为现有 Runtime 提供实验性开发界面，不创建第二套 Agent、审批、恢复或代码执行循环。TypeScript/Vite 页面只调用原 HTTP/SSE、上传和附件接口。原品牌 SVG 的选定副本用于本地构件，原素材目录不改；公开素材来源/使用范围仍按发布策略核对。

DrovApp 新增 `workbench=True` 装配选项，默认提供 `/workbench/`，`/`、`/workbench` 跳转至该路径。固定 HTML/JS/CSS/品牌/许可资产允许无认证 GET/HEAD，使浏览器能够显示登录表单；Host/Origin、直接本地连接、服务生命周期和 URL 凭据拒绝检查仍适用。固定资源映射不读取用户提供的路径，不开放任意文件目录。所有 `/v1/*` 和 `/openapi.json` 仍需 Bearer；关闭工作台不关闭 HTTP。

新增 `GET /v1/agents -> AgentCatalog`，经认证及 `read` 的 `Resource(owner=actor)` 授权后，只列当前注册的 name/revision；不返回 instructions、工具 schema、模型/后端配置或凭据。新增 `GET /v1/session?session_id=...` 与原路径读取复用同一函数和Runtime授权，供包含斜杠、点段或保留字符的合法会话ID无损查询；原路径接口保留，不缩窄SDK标识类型。这个应用级目录不表示可以启动任意 Agent/会话，start 仍按实际资源授权；宿主需把愿意向该应用读者公开的 Agent 注册到本服务。没有修改 Runtime/存储/旧事件 DTO 或 DDL，新增 HTTP schema 从真实类型/路由生成。

登录令牌只在当前 Client 内存中；提交后清空密码输入，不写 URL、cookie、local/sessionStorage、日志或 HTML。刷新后重新登录。认证失败清除连接和正文；正文/文件/上下文读取被拒绝时撤销 Blob URL、取消旧视图并清除已取正文，后到的旧请求不能恢复它。默认元数据视图，用户显式打开正文。浏览器不显示 raw provider protocol items；所有服务返回文本用 DOM textContent 展示，不执行 HTML/Markdown。页面没有外部字体、脚本、统计或 CORS 请求。CSP 限定脚本/样式/连接同源，图片仅同源或本地 Blob，禁止 frame/object/base。

## 运行、交互与文件

事件使用带认证 fetch；客户端只承认与当前 Run/seq 完全一致的 durable id，用 seq 合并，临时帧不更新游标。不完整帧不确认。断连按有界退避从原游标重连，临时 token 可能缺失；不能自动 start、批准、cancel、verify 或 recover。永久游标错误要求刷新快照。主动取消/恢复分别提交原接口；响应显示持久回执，不把回执当完成。

开始运行时保存本次请求的不可变 body/request_key。网络丢应答或 5xx 后，显式重试使用同一声明；交互响应丢应答同样复用原 request_id/response。不会用新键猜测重执行。格式/权限/冲突仍由服务判断，UI 不改变工具参数或原计划。核查器返回仅作为建议展示，applied 的可选 result 保持缺省与显式值的区别。

上传通过原私有暂存/Run 同事务消费；暂存不等于已接收。选定的 Agent/会话在文件暂存期间保持固定。附件名只从正文授权的 ArtifactInfo 取得；每次下载/预览重新获取授权元数据、受控读取、核对长度和 SHA-256，并在展示前再检查权限。仅 image/png 可尝试预览，检查真实签名/IHDR/像素上限并交给浏览器解码；HTML/SVG 和其他文件统一作为 octet-stream 附件，不建立 iframe/object 或 HTML Blob 页面。UI 限额在 [工程方案](../planning/engineering.md)；宿主限额和动态权限仍更具约束力。已交付/已下载的历史字节无法撤回，空闲页面不宣称即时感知宿主撤权，下一次同步/读取时清除。

退出码/终止原因来自原 sandbox 结果。仅内置 sandbox 的已发布成功结果表明原销毁已确认；否则标记尚无已发布销毁证据，不根据 failed/cancelled 等状态推断清理成功。查看/预览/下载只读既有事实。E2B 不在本轮 HTTP/UI 的支持声明内；没有借用旧云预算。

## 替代、代价与构建

- 把 token 嵌入页面/URL 可省一次粘贴，但扩大凭据暴露；采用公开空登录壳和内存凭据，代价是刷新重新登录。
- 浏览器直接访问工具/后端会分裂权限和生命周期；全部复用 HTTP，代价是 UI 需处理持久/临时事件、冲突及异步状态。
- 只按后缀内联文件会执行不受信内容；只解码有界 PNG，其他下载，代价是首期无 SVG/HTML/PDF/富文本预览。
- 每个用户构建前端会依赖 Node；统一 wheel/sdist 携带预构建资源，构建维护者固定 Node/npm 和独立锁。SDK 导入仍不加载 Web/Node；安装用户无需 Node。CI 重新生成 schema、构建并比较跟踪资源，防止源码和静态包漂移。

固定工具版本来自本机实际 npm 解析/锁与安装，不把 npm latest 当基线。TypeScript 7.0.2 与 openapi-typescript 7.13.0 的 peer 约束冲突后，选择实际兼容的 5.9.3；没有强制忽略依赖约束。Vite 生成的 module-preload helper 保留 MIT 声明，见 [THIRD_PARTY](../../THIRD_PARTY.md) 与包内许可文件。实施前 P4 指纹保留，差异由本 ADR 和验证记录追溯。

验证入口为 [工作台源码/说明](../../workbench/README.md)、`tests/test_workbench.py`、Playwright 协议/浏览器测试及仓库外安装的实际 HTTP 资产探针；具体命令、失败修正、Docker/平台和未验证范围见 [T-14验证](../validation/t14-workbench.md)。脚本模型不代表真实模型质量。

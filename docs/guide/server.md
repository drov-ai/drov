# HTTP 与本地工作台

## 先验证本机服务

```sh
uv run --locked --extra server python examples/http_offline.py
uv run --locked --extra server python examples/http_offline.py --stream
```

例子启动随机 loopback 端口，使用临时请求头凭据运行三个应用，最后关闭服务；不会显示令牌或访问外部模型。实际路由/请求 schema 见[OpenAPI](../reference/openapi-v1.json)，服务的 `/openapi.json` 也要求认证。

`DrovApp` 是同一 Runtime 的 ASGI 入口，lifespan 可持有自有 Runtime 或借用已打开的 Runtime。`local=True` 仅服务 loopback，生成本次临时 UI 令牌；外部部署必须提供自己的 AuthAdapter、可信身份、TLS 和访问控制，不能信任请求 JSON 中自报的 tenant/user。不要开启多个 worker 共同打开同一 SQLite 数据库。

## 打开工作台

```sh
uv sync --locked --extra server
uv run --locked python -m examples.workbench_offline
```

在本机打开终端显示的地址，将这次的 UI 访问令牌粘贴到登录表单。它不是模型/云 Key。令牌仅存页面内存，刷新后重新登录。不要把启动输出贴进公开 issue。

样例支持检索、Memory、输入/审批和合成事项写入；`fail` 检查失败，`slow` 检查取消与重连。显式使用已准备的本地 Docker 时：

```sh
uv run --locked python -m examples.workbench_offline --docker-config .drov/sandbox-environment.json
```

这份配置属于当前机器，换机器按[Docker 教程](sandbox.md)重新准备。选择 csv-sandbox 上传 `numbers.csv`（value 列，12、18、30），查看代码和输入后批准，读取 summary.csv/chart.png。工作台不会独立执行工具或直接连接模型/云；它使用原 HTTP。

## 正文、流与下载

默认只显示元数据。打开“显示正文”才读取有权限的输出、上下文、代码、文件名和下载。撤权会在下一次操作/同步清除视图与 Blob URL，已下载字节无法撤回。HTML/SVG 不内联执行，仅受支持 PNG 可预览。

SSE 临时 delta 可能丢失，使用持久事件游标补读；断开流不取消 Run。提交回包丢失后复用原 request_key/body，人工响应重试复用原 request_id/响应内容，不生成新的业务执行。工作台最多展示有限事件，不是完整导出工具。

## 部署前条件

本仓库没有托管的 Agent 服务。宿主负责身份映射、TLS、凭据注入、SQLite/字节配对目录与备份、后端收尾、访问日志脱敏和更新。静态文档站不能运行 Python Runtime；Cloudflare Pages 构建仅用于文档。

本地文件/沙箱要求 POSIX 控制与受测 Linux Engine；Windows 可以使用无 LocalArtifacts 的核心/HTTP/工作台。实验性 UI 的范围和开发方式见[工作台说明](../../workbench/README.md)，威胁边界见[限制](limits.md)。

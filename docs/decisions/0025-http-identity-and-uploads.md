# ADR-0025：HTTP 身份、订阅与上传接收

日期：2026-10-15。状态：工作决定；实施和验收状态以 [progress](../planning/progress.md) 为准。

## 原因与决定

T-13 将传输接入现有 Runtime，不建立第二个执行循环。`drov.DrovApp` 是可调用 ASGI 对象，构造时才加载 server extra；`drov` / `drov.server` 的导入不加载 Web 栈。装配 agents/storage/policy/authorizer/dependencies/artifacts/limits 与借用 runtime 两种方式互斥；前者在 lifespan 打开/关闭自己的 Runtime，后者要求已打开且不关闭宿主 Runtime。`serve()` 固定单 worker、关闭代理头信任，ShutdownIncomplete 仍由宿主观察并重试关闭，不伪装释放锁。

认证必须显式选择 local=True 或 AuthAdapter。本地模式只绑定 127.0.0.1，lifespan 生成本次随机 Bearer token，宿主通过 local_token 在本机取用，不自动打印/写盘。检查直接连接的 peer/server、精确 Host 与 Origin；认证 fetch 使用请求头，URL 凭据拒绝，未知身份不信任 X-User-Id/X-Tenant-Id。应用模式 AuthAdapter.authenticate(credential) 产生 RunContext，非 loopback 绑定另要求显式 Authorizer/允许 Host/同源 Origin；TLS、账号和轮换由宿主负责。首期不提供任意跨源 CORS、cookie 登录或完整 IAM。

HTTP start/respond/cancel/recover 返回内核回执或快照；GET Run 返回 RunInspection，failed outcome 的获取仍为 200。cancel/recover 发送空 JSON 对象，verify 只收 request_id。错误只返回安全 code/message/request_id，不回显请求输入、验证细节或异常正文。请求 JSON 同样拒绝重复键/非有限值；正文、连接、上传并发与发送期限有界，数值唯一见 [工程方案](../planning/engineering.md)。OpenAPI 从实际路由生成，需认证获取；不会列出 Agent 配置或后端凭据。

SSE 先验证读权限与完整游标再发送头。每帧复用 RunEvents 的当前授权投影，重新认证并固定为原 actor；只有 durable 帧有 run_id:seq，Last-Event-ID/after 必须一致。慢发送直接背压，无额外无界生产队列；临时增量沿内核限额丢弃并发 gap，durable 从日志补读。断连/发送超时/应用关闭只关闭订阅；显式 cancel 或 owned Runtime 的关闭才影响执行。头后授权/游标等错误用无 id、durable=false 的 stream.error 结束；已交给传输的旧字节无法收回。

sessions/session 在 Runtime 内实现本人的 scope 查询、逐资源授权与有界 keyset 分页。游标用 SQLite/MemoryStorage 原 secret、独立域和查询/身份绑定的 HMAC；篡改/跨查询/跨身份拒绝。分页不授予跨用户会话枚举，跨用户已知 Run 继续沿 Authorizer 查看。

## 文件与事务

POST /v1/artifacts 使用 application/octet-stream 原始单文件流，元数据为显式 query：agent_name/revision/session_id/name/media_type；要求 Content-Length，先按配置及 owner/store 容量预留，再有界接收。只返回短期 upload_id/expires_at，不返回 ready ArtifactRef。元数据和字节仍在原 SQLite/LocalArtifacts 配对中，不使用 HTTP 临时目录作为权威存储。

新增独立 upload_schema=1 / artifact_uploads 扩展，只在带 ArtifactStore 的 DrovApp lifespan 显式启用；原 records、artifact_schema、sandbox_schema 和历史基线不改。记录 owner、目标 scope/Agent 配置 hash、store/stage、期限、receiving/staged/consumed、版本与源 Run。私有字节台账照常计费，包括未完成、孤立/过期暂存；TTL 到期拒绝新消费，不默认删除。T-15 才实施持排他锁的维护回收，不能在本轮宣称已回收。

RunController 在接收事务中按固定 upload_ids 查验归属/配置/期限/完整字节与授权；标记 consumed、发布输入元数据/授予和 start 去重同成同败，无二次拷贝或重算。相同 request_key/相同上传声明返回原 RunRef；已消费 ID 换接收键冲突，猜 ID/换 tenant/user/Agent/revision/session 隐藏。ready artifact_ids 走原授权引用复用。组合文件数量/大小/重名仍受同一内核限额约束。

元数据默认隐藏逻辑名；下载先 read/view_content、hash 校验，发送每个有界块前再认证/授权。使用 octet-stream + attachment、编码后的 filename*、nosniff/CSP，HTML/SVG 不内联。已不可用内容为 410，存储暂不可用为 503；撤权后停止后续字节，不声称能撤回已交给 socket 的数据。没有宿主路径、后端 URL、镜像/挂载/密钥的 HTTP 配置入口。

## 替代与代价

- 每请求创建 Runtime 会破坏任务/连接/关闭所有权；采用单个 lifespan Runtime，代价是首期仅支持单 worker 和排他库。
- 在上传时创建孤立 ready 输入会绕过 Run 授予；采用暂存和同事务消费，代价是新增私有扩展及独立清理责任。
- multipart/不定长上传增加解析/临时文件与资源规则；首期选择明确长度的原始流，客户端需先知道大小，未知长度为 411。
- 只在建立 SSE 时授权会把预取页当持续授权；采用逐帧/逐块复核，代价是额外授权与存储查询。

旧 SDK 对新上传扩展库按未知表拒绝打开、保持原库不变；这是新增私有扩展兼容边界，不是 records 自动迁移。没有 HTTP 文件的旧库仍照原格式读取。普通 SDK start/run/stream 的公共参数、ArtifactRef/运行状态与原执行协议不变。

## 来源与验证

2026-10-15 查阅官方 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[Starlette StreamingResponse](https://starlette.dev/responses/)、[Starlette request stream](https://starlette.dev/requests/) 和 [Uvicorn flow control](https://uvicorn.dev/server-behavior/)。这些是当日在线文档，固定实现依据为已安装/锁定 FastAPI 0.143.0、Starlette 1.7.0、Uvicorn 0.54.0；没有把文档 latest 当固定版本。无上游代码拷贝。实际 ASGI 2.0/2.4、真实 loopback、Docker/安装与平台证据见 [T-13验证](../validation/t13-http.md)，不由 ADR 推断通过。

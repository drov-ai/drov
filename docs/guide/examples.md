# 三个参考应用

从仓库根目录运行。以下例子的模型是 ScriptedModel 或等价确定性夹具；工具、SQLite、子进程与 loopback 服务实际执行。它们检验机制，真实模型质量另见[版本说明](release-notes.md)。三类应用不是产品业务边界。

## 研究与检索

```sh
uv run --locked python examples/research_offline.py
uv run --locked python examples/context_preview_offline.py
```

第一个例子实际检索两条缓存资料并核对 Report 的引用。第二个返回长资料，检查有版本/hash 的预览和 ContextManifest；报告明确没有读取全文。两者支持 `--stream`，检查最终结果和已提交事件。

## 个人助手与 Memory

```sh
uv run --locked python examples/memory_offline.py
uv run --locked python examples/memory_offline.py --stream
```

示例创建临时合成库，七个独立进程依次验证保存、跨会话读取、其他用户/租户/Agent 隔离、删除和删除后读取。检查真实版本与事务事件，不从模型“已保存”的自述推断成功。临时数据自动清理，不操作你的业务数据库。

## 业务操作与人工控制

```sh
uv run --locked python examples/interactions_offline.py
uv run --locked python examples/operations_offline.py
uv run --locked python examples/operations_offline.py --stream
```

前者跨进程检查 HumanInput、批准/拒绝和重复回执。后者启动合成事项服务，实际检查单次写入、回包丢失后的核查、未知效果与取消。重复核查不能多写一次；不把“请求超时”解释为“没有执行”。

## CSV 与图表变体

[Docker 教程](sandbox.md)运行真实 CSV → Python → CSV/PNG → 销毁后授权下载。模型代码是预设夹具；没有 daemon/固定镜像时跳过这一条，不影响上述核心示例。真实模型生成代码的独立证据保存在 T-16，不能把这条预设示例计入模型质量。

## 其他入口

| 入口 | 命令 | 实际验证 |
| --- | --- | --- |
| Responses | `uv run --locked --extra openai python examples/openai_responses_offline.py` | 官方客户端仅访问 loopback HTTP/SSE，实际工具结果 36 |
| DeepSeek 方言 | 上一命令加 `--deepseek` | 本地明文协议续传；仍无外部请求 |
| MCP | `uv run --locked --extra mcp python examples/mcp_stdio_offline.py` | 实际 stdio 服务、固定发现、审批、进程关闭 |
| HTTP | `uv run --locked --extra server python examples/http_offline.py` | 三应用共用 Runtime、真实 loopback HTTP |
| SSE | 上一 HTTP 命令加 `--stream` | 真实 SSE 传输和原暂停/结果 |
| 维护 | `uv run --locked python examples/maintenance_offline.py` | 临时合成库预览/逻辑清理；POSIX 另查字节回收 |
| 备份 | `uv run --locked python examples/backup_offline.py` | 实际 backup/restore，副本屏障阻止新任务 |
| 迁移 / 导出 | `examples/migration_offline.py` / `examples/export_offline.py` | 兼容字段回填 / 授权副本，均用临时合成数据 |

离线例子均不读取真实 Key。只有工作台启动样例会在本机显示临时 UI 访问令牌；它不是模型 Key。[完整示例目录](../../examples/README.md)说明每一条的夹具和限制。

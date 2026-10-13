# 离线检索与上下文检查

在仓库根目录完成 `uv sync --locked` 后运行，不需要账号、API Key 或私有资料：

```sh
uv run --locked python examples/research_offline.py
uv run --locked python examples/research_offline.py --stream
```

例子对应 S-01-A：真实 Python 工具从自有合成语料检索 A/B 两条资料，ScriptedModel 检查实际工具结果，再返回预设 JSON；最终输出严格校验成 Report，并核对引用集合与实际来源一致。`--stream` 用相同 Runtime 入口打印已提交事件，随后读取相同结果。

这是可执行的离线契约验证，回答是预设夹具，不证明真实模型的推理质量。运行只用内存存储；SQLite、去重、取消、正文授权与失败提交另由集成测试验证。当前只支持 read 工具，ASK 失败为 interaction_unavailable；Memory、审批恢复、真实模型和 HTTP 尚未交付。完整范围与下一项见 [progress](../docs/planning/progress.md)。

T-07 的 S-01-C 预览/引用检查：

```sh
uv run --locked python examples/context_preview_offline.py
uv run --locked python examples/context_preview_offline.py --stream
```

实际工具返回长合成资料，脚本模型只检查带引用/版本/hash 的标记预览；类型化报告明确未读取全文。示例从 inspect 的摘要索引读取 get_context，核对来源版本、preview 处理、粗估与预算。来源引用定位已保存的操作事实，不额外执行工具或调用模型；正文需 include_content=True。这个例子不证明真实模型会正确理解预览。

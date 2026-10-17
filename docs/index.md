---
title: Drov · Python Agent 框架
---

<div class="hero">
<p class="eyebrow">PYTHON AGENT FRAMEWORK · 0.1.0a0</p>
<h1>让 Agent 的每一步，<br>都有据可查。</h1>
<p class="hero-lead">从一个 Python 工具开始，连接上下文、记忆、人工审批与代码执行。用同一套 Runtime 运行、查看和恢复。</p>
<div class="hero-actions"><a class="button primary" href="0.1.0a0/quickstart/">运行第一个 Agent <span aria-hidden="true">↗</span></a><a class="button" href="0.1.0a0/api/">查看 Python API</a></div>
<p class="fine">开发快照 · 从源码安装 · 首个例子无需 API Key</p>
</div>

<div class="runtime-strip" aria-label="运行过程"><span>输入与身份</span><b aria-hidden="true">→</b><span>模型与工具</span><b aria-hidden="true">→</b><span>持久事实</span><b aria-hidden="true">→</b><span>结果与检查</span></div>

## 从定义到运行，再到查看

Agent 描述模型、工具和输出类型；Runtime 管理资源、权限和执行。SDK、流式入口与 HTTP 使用相同内核，工作台呈现保存的事件与交互。

```python
async with Runtime(agents=[agent]) as runtime:
    outcome = await runtime.run(
        agent,
        "比较两种缓存方案",
        session_id="research",
        context=context,
    )
    inspection = await runtime.inspect(outcome.run_id, context=context)
```

这是调用形态片段。[快速入门](guide/quickstart.md)包含可直接执行的完整定义；[运行原理](guide/concepts.md)解释每一步保存了什么。

<div class="feature-grid">
<section><span class="section-number">01 / CONTROL</span><h3>在产生效果之前，先决定权限</h3><p>工具声明 read、write 或 unknown。人工输入、审批和未知结果核查持久保存，继续原计划；外部写入不自动重试。</p><a href="0.1.0a0/examples/">走一遍审批流程 →</a></section>
<section><span class="section-number">02 / INSPECT</span><h3>知道模型看到了什么</h3><p>检查上下文来源、版本、选取与预览。Memory 显式写入，有版本和作用域；正文查看始终受当前权限约束。</p><a href="0.1.0a0/concepts/">理解上下文与记忆 →</a></section>
<section><span class="section-number">03 / EXECUTE</span><h3>从文件输入，到可下载产物</h3><p>受控 CSV 进入单次 Python 沙箱。代码经审批、导出有界文件，确认执行环境销毁后发布结果与产物。</p><a href="0.1.0a0/sandbox/">配置 Docker 文件分析 →</a></section>
</div>

## 用不同应用检验同一套抽象

| 从这里尝试 | 你将检查什么 |
| --- | --- |
| [研究与检索](guide/examples.md) | 实际工具来源、严格类型输出、长资料预览 |
| [个人助手与 Memory](guide/examples.md) | 跨进程记忆、用户隔离、删除墓碑 |
| [业务操作助手](guide/examples.md) | 澄清、审批、单次写入与未知效果核查 |

三个参考应用用于检验通用性。Drov 的边界是通用 Python Agent 框架；多 Agent、Skill 与持久 Notebook 尚未实现。

## 现在可以期待什么

当前版本为 `0.1.0a0`，尚未公开发布 Python 包或官网。固定合成任务的 DeepSeek 评测已达到工作门槛，仍有三项模型失败；这不代表任意任务可靠性或竞品优势。OpenAI 路径仅完成离线协议验证，工作台为实验性功能。

先阅读[能力与平台限制](guide/limits.md)，再选择[源码安装](guide/quickstart.md)或[HTTP 与本地工作台](guide/server.md)。完整结果与局限见[版本说明](guide/release-notes.md)。

# T-07 上下文选择与检查清单验证

- 日期：2026-10-13（Asia/Shanghai），开发版本 0.1.0a0
- 起点：`/Users/yanlong/projects/drov`，main / 0b2ac9b，保留 T-06 未提交变更；重跑上一项 310 项：308 passed / 2 skipped，确认无 T-06 本地缺项后实施
- 契约：API/运行 v3 + [ADR-0011](../decisions/0011-context-assembly.md)；无新依赖，uv.lock、品牌、历史 P4 fingerprint、SQLite DDL 不变
- 环境：macOS arm64，Python 3.11.9 / SQLite 3.45.1、Python 3.14.3 / SQLite 3.52.0；uv 0.13.0
- 状态：T-07 任务卡本地验收完成；T-06/07 保留原 checkout 并保存本地提交检查点，未推送、未运行新远程 CI、未调用真实模型、未发布/部署

## 验收结果

新增 [test_context.py](../../tests/test_context.py) 35 项，全库 345 项；复用 T-06 的失败/取消/时间戳顺序、正文/流/所有权测试。

| 要求 / 关联 | 实际证据 |
| --- | --- |
| 完整组 / C-05、B-15 | 旧轮次的用户输入/助手计划/全部工具结果/provider 一起排除，当前请求仅一次；超大中间轮次排除后可选更旧小轮次，选入顺序保持原历史 |
| 完整来源 / C-07 | 内存与 SQLite 在模型用户代码进入前已保存输入/清单，来源种类/版本/hash 可读；103 个连续 Run 的历史分页保留全部 102 个旧输入的选入/排除清单 |
| 预算 / B-15 | 窗口默认、显式上限、输出预留与未知窗口显式配置均验证；系统规则、输入、工具 schema、输出 schema、请求协议开销分别超限时 attempt=0 |
| 保护协议 / B-15 | 当前 provider 原始协议过大时不裁半个链，下一 attempt=0；同计划两个工具均保留匹配结果和独立来源 |
| 结果预览 / S-01-C | 长中文/引号/反斜杠内容按 UTF-8/JSON 转义有界，带来源 ref/version/hash/原字节数/截断标记；16KiB 默认与紧预算下共享上限缩小均通过；原结果仍完整保存、无重做工具 |
| 预览失败 | 标记/引用无法装入配置上限时有 rejected 清单、context_overflow、不产生下一 attempt；其余保护内容超预算同样失败 |
| 原子边界 / B-03 适用 | 输入已写、context 写入注入 PersistenceUnavailable 时整个事务回滚；无 input/context/context.prepared、无步数增加或模型调用；保留最后成功运行事实 |
| 固定快照 / B-08 适用 | 瞬态错误重试只装配一次，model_call_id/ModelRequest/manifest/input hash 不变，独立 attempts；只有一个 context 记录 |
| 使用量 | 全请求粗估等于序列化请求 + 256 + 每消息/provider 32；供应商报告 input_tokens=17 关联读取，初始清单仍为未知，不改写装配快照 |
| 查询投影 / B-10 | 默认 sources 正文全为 None；正文需要 read/view_content，读取期间撤权后再次检查拒绝；Bob/跨 Session/错误 call 引用不可读；provider 正文始终不展示但计入粗估 |
| 分页索引 | limit=1 的每个事件页只索引对应 context.prepared，sources=[]、content_visible=False；索引不读取详细 context/input；顺序遍历等于实际请求 ID，无重复无遗漏 |
| 持久与完整性 | SQLite 重开清单一致、没有新模型调用；修改返回 DTO 的 sources 不改写存储；输入 hash 不匹配或历史 schema_version=99 时拒绝，不重写旧事实/不派发 |
| 实际示例与安装 | [预览例子](../../examples/context_preview_offline.py) 普通/stream 入口检查可见来源 ID，Report 明确 full_document_read=False；两解释器各两个干净核心安装均运行检索/预览四种入口 |

最终 Python 3.11：**343 passed / 2 skipped**，均为 Python 3.12+ 即时任务工厂 API；Python 3.14：**345 passed**。Ruff、格式、严格 mypy（55 source files）、公共/存储 schema 漂移检查通过。

过程中发现的全库失败是 T-04/T-06 旧“未来入口不得存在”断言仍包含 get_context；在实际查询已实现后移除该项，后续 recover/respond/resume/verify 仍不得有空壳，最终完整回归通过。新增测试初次调用遗漏 ModelResponse/ToolCall 导入，补齐后通过；不把早期失败运行列为验收成功。

## 实际检查命令与产物

```sh
.drov/tooling/bin/uv run --locked pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 .drov/tooling/bin/uv run --locked --python /opt/homebrew/bin/python3 pytest
.drov/tooling/bin/uv run --locked ruff check .
.drov/tooling/bin/uv run --locked ruff format --check .
.drov/tooling/bin/uv run --locked mypy
.drov/tooling/bin/uv run --locked python scripts/generate_schemas.py --check
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
.drov/tooling/bin/uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
.drov/tooling/bin/uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
git diff --check
```

两解释器各构建 wheel/sdist、从 sdist 重建并比较相同 wheel payload，分别在两个干净核心环境中安装/检查兼容依赖、验证隔离导入、从仓库外运行安装包的 research_offline/context_preview_offline 普通与 stream 入口。wheel 仅含 drov/元数据；sdist 包含两个示例及源码/文档，环境/品牌/数据库排除。最终构件保存在 `dist/` 与 `.drov/dist-py314/`，构建安装摘要见各目录的 `package-check.json`；只保存本地，不上传。

文档相对链接、Python 片段语法、案例/能力引用、受保护文件未变和可识别凭据模式计数检查保存到本地 `.drov/t07-doc-check.json`。只扫描本轮 Git 变更范围、只输出命中数量，不读取环境密钥；模式检查不是任意秘密内容的安全证明。

## 未实现、未验证与恢复步骤

- T-07 卡无剩余本地验收项。B-08 的 required/best_effort 时序和 Memory 子预算仍待 T-08；当前 retrieval_status=disabled，没有伪造记忆来源。
- 字节粗估不是精确 tokenizer 或硬上界；完整来源清单需扫描已提交会话，显式详细查询的规模随候选增长。只验收了 103 轮正确性，没有声明超长历史性能。
- 预览引用指向已保存的内部工具操作，未增加全文检索工具。预览不表示模型读到全文，不能把 ScriptedModel 断言当真实推理质量。
- T-06 存量输入无 context，不回填伪造来源；T-06 的无清单 context.prepared 事件可继续读取状态/事件，get_context 明确不存在；新清单按实际 model_call_id 查询。显式恢复/迁移尚待 T-09/T-15。
- 本轮仅 macOS 3.11/3.14 本地检查；未跑新远程/跨平台 CI、真实模型、HTTP/工作台或付费调用。旧 CI 只支持旧提交。跨归属正文查询、Memory、审批/未知写入不宣称已支持。
- 恢复：继续当前目录，核对本地提交、progress/ADR-0011，运行锁定检查；按 T-08 卡接入 Memory 原子工具/检索并保留本轮回归。用户当前无需新账号/模型 ID/Key；真实服务和预算在 T-11/T-16 前另行明确，聊天不贴凭据。

任务/用户准备/会话切换唯一维护在 [progress](../planning/progress.md)。

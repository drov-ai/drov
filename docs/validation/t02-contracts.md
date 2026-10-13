# T-02 公共类型与适配协议验证

- 日期：2026-10-13（Asia/Shanghai）
- 代码：本地开发版本 0.1.0a0，Git 未提交/无远程，不能提供 commit 或远程 CI 通过结论
- 基线：P4 API/运行契约 v3 + [ADR-0006](../decisions/0006-python-contracts.md) 的细化；旧 P4 指纹未覆盖
- 环境：macOS arm64，Python 3.11.9 / 3.14.3；Pydantic 2.14.1、pydantic-core 2.50.1、typing-extensions 4.16.0；准确依赖见 uv.lock

## 交付与验收对应

| T-02 交付 | 证据 |
| --- | --- |
| Agent 定义/配置 | tests/test_model_protocol.py：构造不请求模型、不关闭实例；name/revision、缺能力、未知窗口/输入预算前置拒绝 |
| 状态/结果/事件/交互/上下文/分页类型 | tests/test_types.py：JSON 往返、四态判别、非法 output 拒绝、UTC、版本、归属一致、正文/元数据形状 |
| Limits/Usage/ModelCapabilities | 正数/有限数/额外字段/严格类型；0 次重试；unknown 保持 None；管理员 ceiling 不得放宽 |
| 工具规范化/可信注入 | tests/test_tools_schema.py：嵌套对象/枚举/可空/默认值、Field 约束、字节限制、ctx/tenant 注入拒绝、额外字段和强转拒绝 |
| schema 支持边界 | 不支持类型/签名/自定义结构明确拒绝；最终 output 只接受 str/对象；工具结果字典允许，但不伪造供应商严格对象支持 |
| ModelAdapter 协议/夹具 | tests/fixtures.py / test_model_protocol.py：预期输入、完整响应、opaque protocol 往返、部分流/重复完成/重复 call_id/异常 |
| 生成 schema | scripts/generate_schemas.py、docs/reference/schema-v1.json、test_schema_snapshot.py；由类型生成，--check 验证漂移和 discriminator |
| 交接 | docs/handoff.md、AGENTS.md；当前资源/下一项/换会话节点在 progress 唯一维护 |

C-01/02/04/12 的类型接点已交付，不等于这些能力的运行验收全部完成。B-15 的 schema/协议边界已测，上下文预算/完整历史/恢复仍由 T-07/T-11 等任务验证。

## 实际检查

维护者使用 `.drov/tooling/bin/uv`（0.13.0）；以下为等价通用命令。Python 3.14 使用独立环境 `.drov/check-py314`，明确指定解释器，未改变默认 3.11 环境。

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/generate_schemas.py --check

UV_PROJECT_ENVIRONMENT=.drov/check-py314 uv run --locked --python /opt/homebrew/bin/python3 pytest
UV_PROJECT_ENVIRONMENT=.drov/check-py314 uv run --locked --python /opt/homebrew/bin/python3 python scripts/generate_schemas.py --check

uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv
uv run --locked python scripts/check_package.py --uv .drov/tooling/bin/uv --python /opt/homebrew/bin/python3 --out-dir .drov/dist-py314
uv run --locked twine check --strict dist/*.whl dist/*.tar.gz .drov/dist-py314/*.whl .drov/dist-py314/*.tar.gz
```

Python 3.11 和 3.14：各 116 项测试通过，生成 schema 一致。Ruff 和严格 mypy 检查通过。两解释器的 wheel/sdist 构建、sdist 重建内容比较、各两个干净核心环境安装/依赖/导入隔离检查通过；Twine 与工作流语法检查通过。构件及摘要在忽略目录，不发布。

文档检查核对 29 份 Markdown、201 个本地链接、11 个 Python 片段语法、18 项任务/23 条依赖及 schema 源码一致性；定义参考中的已实现 Python 示例实际运行通过；55 个品牌文件与本轮开始摘要一致，P4 baseline JSON 保持原字节。旧 hash 与当前源文档不同是追加实施决定后的历史差异，不能覆盖旧指纹。

## 发现并处理的问题

- Pydantic 泛型模型的旧式联合 alias 在 Python 3.11 会丢失运行时参数；改用 TypeAliasType，用真正的数值拒绝用例验证。
- 核查响应默认序列化 result=None 会把省略变成 null；serializer 保留字段是否存在，并保留默认 kind 判别字段。
- Literal 可能接受 true/1 的相等性；增加类型检查，不让 schema_version/durable/零写入重试含糊。
- Pydantic 支持超过首版约定的特性；增加明确拒绝与负向测试，常见 Field 约束仍同时保留/校验。可空字段的约束必须保留在 anyOf 对应分支，已补最小值拒绝测试；递归类型亦有明确拒绝用例。

## 验证边界

没有运行 Agent 循环、数据库事务、实际工具、权限引擎、MCP、HTTP 或工作台；没有模型 Key、API 调用、费用、真实模型质量或竞品结果。类型检查不能自动脱敏任意正文；授权投影与动态撤权尚未实现。

Linux、Windows、Python 3.12/3.13/3.15 未在本轮实际运行；远程 CI 尚未执行。未重跑所有 extras：T-01 已做依赖组合检查，T-02 新代码在核心安装下确认不会加载它们；真实适配行为由对应任务验证。typing-extensions 由锁中间接依赖改为直接声明，版本未变；其余依赖版本未升级。

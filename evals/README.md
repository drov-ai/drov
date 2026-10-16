# T-16 固定任务评测

[manifest.json](manifest.json)是自有合成任务，三场景各9项，共27项。普通离线每项一次，正式同一模型配置每项3次，合计81试验；Memory多阶段是一个试验，多阶段/工具循环不等于一次模型调用。规则和门槛唯一维护在[质量计划](../docs/planning/quality-plan.md)，状态见[progress](../docs/planning/progress.md)，实际结果见[T-16记录](../docs/validation/t16-evaluation.md)。不以离线FixtureModel当作智能评测。

在仓库或sdist源码根目录、完成锁定依赖安装后：

```sh
uv run --locked --all-extras python -m evals.run --directory .drov/eval-offline --docker-config .drov/eval-docker.json
uv run --locked --all-extras python -m evals.run --directory .drov/eval-stream --entry stream --docker-config .drov/eval-docker.json
uv run --locked --all-extras python -m evals.run --directory .drov/eval-http --entry http --docker-config .drov/eval-docker.json
```

输出目录必须新建，避免覆盖/挑选历史结果。非秘密Docker JSON需要docker_cli、docker_host、固定analysis_image三个字段；使用已验收的分析镜像image.json中实际image_id，工具不自动构建/拉取镜像。缺资源时任务失败/未验收，不把skip算Docker通过。HTTP模式通过真实ASGI应用和授权/上传路由进入同一Runtime，进程内传输不等同真实TCP/SSE网络验收，后者由T-13/T-14和既有专项支撑。

每个阶段新worker进程，保存实际请求/完整响应、用量/延迟、inspect、独立事项计数和下载文件；只含合成数据，原始私有provider continuation只在本地试验目录。报告检查实际状态、Memory内容/版本/事件、真正检索/预览、跨scope输入、审批和独立写入次数、CSV数值/PNG解码、销毁后新Runtime下载/hash与真实产物引用。三个代码任务的离线代码是预定夹具；真实模式完全使用被评测模型的原代码。查看JSON/图片不会重做工具。对照SDK/stream/HTTP时不比较随机ID或不同真实请求逐字答案。

真实运行需要用户先授权服务/model及同一总预算。宿主worker只读取私有、Git忽略env文件中的DEEPSEEK_API_KEY，不执行env语法、不打印凭据或加载E2B项。当前固定[config.json](config.json)是DeepSeek官方/deepseek-flash/none/流式/2048输出，无自动Pro回退；SDK由锁确定。首次付费前提交语料/源码，并将`evals.run.fingerprint()`及config保存为本地freeze JSON（batch_hash/config）；指纹包括SDK、执行器、任务、CSV、配置、项目定义和依赖锁，不包含密钥。

```sh
uv run --locked --all-extras python -m evals.run --mode live --suite smoke --directory .drov/eval-smoke --docker-config .drov/eval-docker.json --freeze .drov/eval-freeze.json --ledger .drov/eval-ledger.json --init-ledger
uv run --locked --all-extras python -m evals.run --mode live --suite formal --directory .drov/eval-formal --docker-config .drov/eval-docker.json --freeze .drov/eval-freeze.json --ledger .drov/eval-ledger.json --smoke-report .drov/eval-smoke/report.json
```

smoke全部7项通过且指纹相同才允许正式批次；不使用--task挑样本取得完整报告。账本不能重建/自动退款，单次调用先持久预留，未知/未结束费用立即停止新增付费请求，保留所有失败/未执行项并核对实际供应商用量/账单，再明确记录恢复决定。运行器目前拒绝覆盖原目录，不自动重跑中断试验；新批次仍需包含历史未结费用、保持一次授权总预算，不能通过新建账本绕过预算。不把保守高峰费用估计写成供应商实扣，不从滚动模型名称推断固定快照。

正式报告保留分子/分母、场景/代码子组、schema、安全、p50/p95、重试、用量/费用、失败人工复核和每场景至少3成功抽查；schema不适用项预先固定，未执行不算通过。维护者在干净环境的使用记录单列；没有独立参与者时仅报告维护者验证，不声称已经证明易用性或竞品优势。

若因明确的提示定义缺口中止smoke，必须保留旧报告，在新真实调用前提交新配置/指纹及原因；`Ledger.amend`只在全部旧attempt已settled且未预算停止时切换登记，保留所有旧费用和原上限。新配置单独完整评测，不能在旧报告中替换失败，不能重建账本退款。当前v2说明及历史失败见验证记录。

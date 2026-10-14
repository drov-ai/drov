# 受控文件/持久产物与可选 E2B 的规划同步

- 日期：2026-10-14（Asia/Shanghai）。起点 Local `/Users/yanlong/projects/drov`、main / fa2b671，工作区干净且与 origin/main 同步。
- 用户已采纳上一轮推荐范围；本轮是具体范围/契约调整，不重新开启 P4 一般规划。
- 类型：Markdown 文档及 Git 差异核查；**没有实现/验收沙箱、文件存储、分析镜像或 E2B**，不复用 T-10 CI 证明新增能力。

## 实际交付

| 维护位置 | 本轮变化 |
| --- | --- |
| ADR-0016 / capability-matrix | Docker 单次 Python、受控文件输入及持久产物首版必交；E2B 单列可选 T-10C，不阻塞 Docker；Skill/持久工作区等后续 |
| API / runtime-contract | 输入 ArtifactInput/Ref、Run 授予与去重/审批绑定、code/inputs/outputs、有界私有存储/下载；暂存/可靠字节/销毁确认/ready 引用和原结果一致、B-18、同一云派发/清理规则 |
| architecture / engineering | 单一运行内核、后端真实能力与控制差异、私有字节/SQLite 配对及阶段所有权；固定标准库/数据镜像、文件/存储限额、路径与链接/竞态防护、可选云配置/费用 |
| reference-scenarios / quality-plan | 强化原 S-01-E/S-02-E/S-03-F 与 X-03：CSV/PNG、销毁后下载、跨会话明确授权；增加文件/磁盘/提交/清理故障与 E2B 独立真实验收；模型试验数量不变 |
| implementation-plan / release-policy | 21 张卡含可选 T-10C；T-10A/B、HTTP/UI/备份/清理/安装发布一并接收文件要求；共享引用/暂存与完整备份规则，公开 E2B extra 须真实验收/独立费用授权 |
| 状态/导航/交接 | progress、product、规划导航、delivery、AGENTS、README、开发/例子/reference、handoff 同步；ADR-0013/范围审查标明历史与后续替代；历史基线仅追加差异说明，原 fingerprint 不改 |

源职责仍以规划导航为准；本页记录变化/验证，不维护第二套公共字段、默认值、评分或任务依赖。

## 验证方法与结果

实际执行：

```sh
python3 .drov/check-sandbox-artifacts-plan.py
git diff --check
```

- 53 个 Markdown、436 个本地链接、14 个 Python 片段 AST、86 个表格列数：错误 0。说明片段仍明确“未实现/不可导入”，AST 不是 SDK 运行验证。
- 21 张任务卡、31 条依赖边：卡片前置与 Mermaid 一致、无环；T-10A 依赖 T-10，T-10B 依赖 T-10A，T-10C 依赖 T-10B；其他卡均不以 T-10C 为前置，原必交集成/发布依赖不丢失。
- 能力/场景/行为/证据/任务引用有效；C-17、B-18、原三个代码案例/X-03 与新增任务在验收中对应。组合编号如 B-16/17/18 按展开后的 ID 核对；首次检查脚本对组合编号与文字断言处理不足，修正检查方法后复查通过，不是 SDK 失败或功能修复。
- 模型评测算术保持 27 任务、81 正式试验、7 smoke、最多 88，门槛 69/22/8 的取整一致；E2B 独立远端/费用不混入该账本。
- 人工复核 API/运行/工程/任务/维护的一致性，明确输出引用顺序/逻辑名、空输入的旧摘要兼容、输入根目录不可替换、冻结不能冒充销毁、最后合法引用清理规则和未 ready 字节的可见性。
- 26 个 Markdown 变更（含新增 ADR/本记录）；src/tests/品牌未变，pyproject/uv.lock、schema-v1、storage-schema-v1.sql、P4 fingerprint 与起点逐字节一致。implementation-baseline 仅追加新差异，原审查正文未改。没有 sandbox/artifacts 公共空壳。可识别密钥模式 4 组匹配均为 0；不读取任何真实凭据文件/环境。
- git diff --check 通过；完整结果保存在本地忽略文件 .drov/sandbox-artifacts-plan-check.json。历史检查 JSON 不覆盖；本轮只改文档，保存检查点使用 skip ci，不重跑未变源码矩阵。

## 未运行与限制

没有更改 src、tests、schema-v1、SQLite DDL、pyproject/uv.lock、品牌或 P4 fingerprint；没有创建 SDK 空壳、安装新库、构建/拉取镜像、启动容器、调用模型/E2B、读取真实凭据或公开发布/部署。本轮文档的 Python 片段只做 AST 检查，不可在当前版本导入。

Docker Desktop 与标准库固定镜像的历史准备成绩仍见 [环境记录](sandbox-environment-preparation.md)；当前镜像没有分析库，T-10A 执行者须构建并核验固定数据镜像/依赖/许可与受控文件闭环。B-16/17/18、真实云 profile 和新记录/存储兼容尚待对应卡，旧 CI 与基础探针不覆盖它们。

## 接续

建议下一轮 New Chat，选择同一 Local 目录，用 [handoff](../handoff.md) prompt 从 [progress](../planning/progress.md) 恢复；下一项 T-10A 的完成条件在任务卡，不预建 T-10C。用户只需保持 Docker Desktop 开启，不需自行寻找 digest 或新账号/Key；分析镜像由执行者准备。

可选 E2B 远端验收前才需账号/区域与 template-build 权限、E2B_API_KEY 本地凭据引用、合成资料上传许可、独立沙箱预算/收尾预留；模型服务/model ID/Key/共同模型预算在 T-11/T-16 live 前准备。发布仓库/包身份/官网账号按 release-policy 到具体外部动作前处理。所有当前未实施卡保留未开始状态，不因规划已同步提前关闭。

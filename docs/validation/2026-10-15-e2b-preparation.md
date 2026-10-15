# 可选 T-10C 准备材料核对

日期：2026-10-15（Asia/Shanghai）。用户询问“补上T-10C需要哪些材料”，未要求启动实施或授权付费云操作。起点Local `/Users/yanlong/projects/drov`、main / fa34c4c，工作区干净、与origin/main同步。任务状态仍只维护在 [progress](../planning/progress.md)；准备材料的源要求为 [发布策略](../planning/release-policy.md#6-需要用户准备什么) 第6节及可选E2B费用条款，范围/前置/完成条件见 [任务卡](../planning/implementation-plan.md) 与 ADR-0016。

## 本次核对

T-10C仍未实施。前置T-10B已完成，T-11也已完成；T-10C独立可选，能够在用户选择后补做，不改变Docker必交链。离线适配/SDK与协议研究无需账号、真实Key或模型；完整关闭仍需真实E2B执行/文件/网络与权限/故障恢复/清理/费用和安装证据。

云端之前用户需明确：可用E2B团队与资源/模板管理权限、区域或数据驻留限制；宿主可安全解析的E2B_API_KEY引用；仅合成代码/CSV/PNG上传许可；允许的并发/资源寿命及独立沙箱预算，含失败与必要清理预留、是否只允许使用赠金。若有现成模板提供非秘密template/build ID；没有则执行者在相应授权和预算内准备并固定模板/依赖。SDK安装、模板配置、版本/许可核验、测试资料与验收批次估价由执行者完成，不要求用户提前制作。无需OpenAI Key/model ID、新仓库或私有业务数据。

凭据留在执行验收的宿主环境或安全凭据服务中，变量名E2B_API_KEY；不粘贴聊天、Git、模型输入、沙箱环境或日志。若采用本地忽略文件，须在实施时明确宿主加载方式与私有权限；当前Drov没有E2B adapter或自动读取该文件的保证。本次没有检查任何凭据值/存在性、创建模板或远端资源。

## 外部来源与适用边界

本次实际打开官方滚动文档（非固定SDK版本）：

- [Quickstart](https://docs.e2b.dev/quickstart)：账号、API Key与E2B_API_KEY配置入口。
- [Internet access](https://docs.e2b.dev/network/internet-access)：默认允许出网，可在创建时控制；Drov选定profile仍须显式禁外连并做真实探针，不能继承默认或从文档推断已经隔离。
- [Pricing](https://e2b.dev/pricing)：页面列Hobby一次性100美元usage credits、默认资源与使用计费、Pro升级选项；用户实际余额/资格/权限以控制台为准。本次不预设必须购买Pro，也不承诺Hobby必能完成全部profile验收；先按实际资源/测试时长估价，模板准备/失败/清理与运行费用纳入独立预算。

旧模板入口返回错误、针对新模板文档的检索未找到结果；没有把它解释为不存在模板机制，也未编造当前SDK/template-build可用性。精确SDK/许可、模板版本固定方法、区域及所需安全能力留T-10C实施时核验。

## 文档修正与验证

发现progress上半部已关闭T-11、下半部“下一项/会话建议”仍保留旧T-11，已在同轮改成T-12，并同步T-10C材料说明；当前任务范围没有变化。本次仅修改Markdown，不改源码/测试/依赖/schema/DDL/品牌；只做相对链接与git diff --check，不重跑未变的SDK或把网页查阅当成E2B实测。

下一步仍由用户选择是否插入T-10C；现有默认下一项T-12保持。进入任一新实施模块均建议New Chat、同一Local checkout；没有准备好E2B资源也能先实施其离线子范围，不能据此关闭整卡。

本轮实际文档检查：2份变更文档、45条相对链接均存在；发布策略锚点按实际第6节标题复核；git diff --check通过。未执行SDK/E2B测试，未调用任何模型或云服务。

用户后续询问E2B_API_KEY定义位置：建议当前checkout根目录 `/Users/yanlong/projects/drov/.env.local`，用户自行填写并设私有权限；项目现有.gitignore的.env.*覆盖该位置。当前没有自动dotenv加载能力，T-10C实施时由授权的宿主验收入口显式加载，不向沙箱注入。已读.gitignore和依赖/开发约定、再次核对官方quickstart；未创建/读取凭据文件或检查真实密钥。仅补充文档，git diff --check通过。

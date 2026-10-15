# T-13 HTTP 与身份边界验证

日期：2026-10-15（Asia/Shanghai）。起点 `/Users/yanlong/projects/drov`，main / 119de61，干净且与 origin/main 同步。已读 AGENTS/progress/导航/handoff，按 implementation-plan 核对 T-10B/T-12 完整验收；实查 3a013be 的核心 38718551848、Docker 38718551936 均 completed/success，无上项缺项。未开启新一般规划。

## 实际交付与环境

按 [ADR-0025](../decisions/0025-http-identity-and-uploads.md) 接通 DrovApp/lifespan、显式本地/应用身份、同一 Runtime 的运行/交互/检查/上下文/会话和 SSE、私有上传台账/同事务消费/附件下载。核心导入不加载可选栈。源码/源契约、schema/OpenAPI、案例、工程、兼容与许可同轮同步；原 records/Artifact/Sandbox DDL、历史 P4 指纹和品牌不改。独立上传扩展会使旧 SDK 拒绝带该扩展的库，后续维护回收仍属 T-15。

本机 macOS ARM64，Python 3.11.9 / 3.14.3，uv 0.13.0。已锁安装 FastAPI 0.143.0、Starlette 1.7.0、Uvicorn 0.54.0。开发组增加既有锁中 httpx 0.28.1，用于独立 server 测试；uv.lock 仅根 dev 声明变化，无解析包版本/hash变化。所有身份、文件、工具和模型输出均合成；没有读取真实凭据、付费模型/云调用、私有上传、公开发布或部署。

## 检查点与失败修正

- 前置含真实 Docker 全库：**1067 passed /61 skipped，270.71s**，`.drov/t13-prerequisite.log`。59 项真实 E2B 缺本轮授权配置，3.11 另两项 Python API 适用跳过；不读取 `.env.local` 或动用旧云预算。
- 首轮 30 项 HTTP 为 28 通过/2 失败，夹具漏 @tool 必填 name，且对终态 recover 误期望成功。按实际既有契约修正夹具，终态 recover 仍冲突，未改内核复活终态。修正后 30 项通过。
- 首轮真实传输 2 通过/5 失败：测试客户端继承了macOS系统HTTP/HTTPS代理而未抵达loopback（环境代理变量均未配置，urllib系统代理scheme实际为http/https）；改用 trust_env=False，额外对响应状态作断言。另修正 ScriptedStep 实际事件参数，并接住 Starlette ASGI 2.4 将发送 OSError 包装后的 ClientDisconnect；没有把 Task 结束冒充内核执行停止。随后 7 项真实传输/阻塞发送通过（1.64s）。原失败日志 `.drov/t13-transport-first.log`，修正 `.drov/t13-transport-second.log`。
- 三参考场景 SDK/stream/HTTP 的 9 项对照、10 项上传错误/原子性、30 项 HTTP 和7项传输通过。补 verify/原保存 final 重开 recover/失败快照与安全500 后，离线 HTTP 合计 **59 passed，3.61s**，`.drov/t13-all-http.log`。代码模型均 ScriptedModel，不把对照当模型质量。
- 真实 Docker 三入口 CSV→统计→summary.csv/实际标准库绘图 PNG→批准/销毁→HTTP hash 下载、无 backend/Agent 的重开下载均通过：**3 passed，3.57s**，`.drov/t13-docker-second.log`。初轮误用不存在的 LeaseTransaction.active 造成3项测试失败，改为现有 active_count 断言后复测；没有跳过真实执行。
- 真实 loopback 普通 HTTP / SSE 示例均通过，三场景输出与单次写入7一致：`.drov/t13-example-http.log`、`t13-example-sse.log`。示例本地 token 只在进程中传递，不输出/入 URL/保存。

## 最终验收（进行中）

完整两解释器含 Docker 回归、构建/独立 core/server 安装、严格类型/生成检查与跨平台 CI 仍在执行；本节完成前不关闭整卡。最终结果与提交/远程 head 将在本节追加。若中断，保留当前 checkout 与 `.drov`/原环境、SQLite/bytes/租约/云账本，先读取 `.drov/t13-{full311,full314,package311,package314,installed311,installed314,core-types}.log`，修正失败后重跑受影响检查；从 T-13 恢复，不进入 T-14。

主要实际入口：

```sh
uv sync --locked --all-extras
python -m pytest tests/test_http.py tests/test_http_transport.py tests/test_http_artifacts.py tests/test_http_scenarios.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest tests/test_http_docker.py -q
DROV_DOCKER_TEST_CONFIG="$PWD/.drov/sandbox-environment.json" python -m pytest -q
python scripts/generate_schemas.py --check
python scripts/generate_openapi.py --check
python scripts/check_package.py --uv .drov/tooling/bin/uv --python 3.11 --out-dir .drov/t13-dist311
python scripts/check_installed_server.py --uv .drov/tooling/bin/uv --python 3.11 --wheel .drov/t13-dist311/drov-0.1.0a0-py3-none-any.whl
```

边界：HTTP 单 worker、精确同源 Bearer 认证，不自带账号/TLS/IAM。上传是已知 Content-Length 的原始单文件传输，不支持 multipart/未知大小；未消费/失败/过期暂存仍占容量，不自动回收；清理/备份在 T-15。下载按内核 hash/权限检查，有界块发送无法撤回已发送字节。SSE 控制错误无 durable cursor；token 不保证重放，持久事件可按原游标补读。工作台/真实模型/公开发布未做；本轮未调用 E2B，不将历史 E2B预算/质量写成本轮结果。


收尾前修正：core-only严格mypy发现可选依赖缺失时继承StreamingResponse成为Any基类。订阅所有权改由外层DrovApp ASGI请求的finally持有/关闭，返回普通StreamingResponse，避免核心类型检查依赖Web安装；两套严格类型（150文件）及59项HTTP回归均通过（4.49s），接口/行为未改。初批完整回归3.11 **1129+61skip，290.58s**；3.14 **1130+59skip/1失败，317.06s**。失败是既有Responses Retry-After日期在收集时生成，长回归运行到该用例时已老化超过原100s窗口；改为该用例开始时生成，保留真实官方客户端/HTTP日期解析与数值区间，生产适配不改。原失败日志保留，修正后两套HTTP+Responses专项均 **120 passed（5.15s/4.67s）**。追加文件发送中途撤权及未知/不符长度测试后，当前完整收集 **1192项**，新增HTTP **64项（61离线/传输+3真实Docker）**；最终全库另记，不把旧59项/初批构件冒充最终。

初批两解释器wheel/sdist重建、四套core-only新环境安装/示例及两套独立server安装后的真实HTTP/SSE三场景均通过，`.drov/t13-{package,installed}{311,314}.log`。该wheel在订阅所有权修正之前，只作历史检查点；最终构件/安装随后补记。core-only严格mypy150文件通过，`.drov/t13-core-types-fixed.log`，含extras版本同样通过。原T-12旧wheel实际仓库外安装后拒绝新upload_schema=1样本库（IncompatibleStorage），主DB前后SHA256均 **8ed238a0736bcaa3415112528a136b382f761b39ef04fae6d2f89333135924e8**，未修改原库；原本无上传扩展的旧库回归由现有存储测试覆盖。

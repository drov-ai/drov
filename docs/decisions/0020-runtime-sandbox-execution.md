# ADR-0020：Runtime 沙箱派发、执行记录与产物发布

- 日期：2026-10-14（Asia/Shanghai）
- 状态：已实施，最终验收证据见 [本轮验证](../validation/t10a-runtime-sandbox.md)
- 关联：ADR-0016/17/18/19、T-10A、B-16/17/18；T-10B 深入故障矩阵仍后置

## 原因与决定

接通固定 `sandbox.run_python` / PythonSandbox、DockerSandbox、SandboxLimits、SandboxResult。工具 effect 固定 write，默认 policy/ask，可 required 或由应用显式 allow；只接受 code/inputs/outputs。临时 Agent.run、无 SQLite/配套 LocalArtifacts/已配置后端拒绝。Backend 为 owned/borrowed/factory 资源，构造无 I/O；Runtime 预检及接收/派发前复核，不拉镜像、联网安装或宿主回退。

静态 Agent 指纹包含后端 dependency 名；实际 backend hash（Engine/CLI/image/profile/限额与固定程序 hash）保存到 Run 的 sandbox_bindings，并进入接收去重摘要。无沙箱清单不改变旧摘要、不写该 Run payload 字段。ArtifactStore 指纹即使无输入文件也固定。审批中的服务器字段 `_resolved_inputs` / `_backend_hash` 加到原参数摘要；原 request_hash 绑定归属、代码、解析输入 hash/清单、输出与后端，模型不能提交这些字段替代内核解析。

SQLite **显式 sandbox extension v1** 增加 sandbox_leases 表与 active 索引/metadata 键，不改旧 records DDL/user_version/record schema=1。Lease payload v1 保存作用域/Run/operation、固定 execution_key、完整非秘密 backend 配置快照/hash、code hash、输入/输出、create/exec-create/exec-start 标记、原 ID、观察/暂存/销毁证据和版本。派发预算、operation/event/检查点与 active/create intent 在同一事务保存，再请求创建。计数包含旧未清理租约；不自动排队、重置或重试代码。旧 SDK 因未知扩展表拒绝该库，新 SDK 读旧普通库。旧 fingerprint 是历史记录，不回写。

## 本地 Engine 与非 root 可信程序

保留固定 argv 的本地 Docker CLI 管理容器；增加有限 **Unix Engine API v1.41** exec-create/start/inspect 子集。CLI 的 exec 把两个动作合并，无法在启动前保存 exec ID；分开调用才能持久化原 ID/start intent，并从 Engine inspect 取真实退出状态，不信任代码 stdout 自报执行/清理。HTTP header/body/frame、原始 stdout+stderr 和期限有界，无请求自动重试、远端 endpoint 或新 Docker SDK 依赖。

容器 PID 1/安装与导出器为 **UID 65534**；工作负载及其子进程为 **UID 65533**，全部非 root/cap-drop=ALL/no-new-privileges/固定 seccomp，根目录只读。可信工作负载 wrapper 在创建原代码子进程前禁用 dumpability，防代码通过同 UID 的 `/proc` 替换其内存/私有描述符；原代码作为容器内独立 Python 子进程参数，宿主不 exec/eval。代码退出后 wrapper 停止/杀死该 UID 的遗留进程，PID 1 回收孤儿；wrapper 被代码杀死得到真实非零退出，不能进入正常导出。T-10B 仍须完整验证恶意/资源/故障边界，不能把本机通过解释为敌对公网强隔离。

Docker 27.0.3 的 cp 不能完成这里的只读根/tmpfs 输入与控制文件传输；采用固定可信入口的 stdin/Engine 数据通道。`/work/inputs` 为 **16MiB 私有 tmpfs**，由 UID 65534 持有，最多 15MiB 输入字节、预留目录开销；安装完成输入根/ID 目录 0555、文件 0444，代码 UID 不拥有且不能替换祖先。位置保持 `/work/inputs/<artifact_id>/<name>`，不改变前批上下文 v2。`/work/outputs` 由可信 UID 创建，代码仅写指定私有工作区，旧环境不保留。

导出器在原 workload exec 退出且遗留 workload 已停止后，以不同 UID、dirfd/O_NOFOLLOW/O_NONBLOCK 遍历声明，检查普通文件/UID/nlink/大小及读前后 inode/stat，边读边限、传输/hash 校验，不在宿主解压 tar。当前同一集合拒绝重复 basename（ArtifactInfo/私有暂存使用单段附件名）；安全相对嵌套路径仍可用。CSV 为附件；`.png` 必须通过 SDK 标准库 PNG 解码 profile：非交错静态图、合法 chunks/CRC/zlib/scanlines/filter/palette，最多 16Mi pixels/64MiB 解码；拒绝压缩元数据/APNG/未知 ancillary，其他文件默认 octet-stream。选择有界 profile 而非给核心加 Pillow 依赖；超出 profile 的合法 PNG 也可能被拒绝，需明确错误而非不安全预览。

## 收尾、发布与恢复

原观察先持久保存，导出全集合可靠暂存再确认整个容器销毁。同事务发布 ready ArtifactRecord、原 SandboxResult/operation/event/检查点及释放 active lease；任何提交错误保留阶段并要求显式 recover，不把已执行代码重跑。tool.succeeded 表示有效观察及销毁/发布确认；正常代码还需 termination=exited / exit_code=0，文件成功还需 artifact_status=complete。

超时/输出超限/取消都由独立 control_seconds 总期限销毁原容器；CLI/stream Task 结束不作销毁证据。未知创建应答且查询不存在不能清租约；未确认资源或未发布结果仍占额，关闭可 ShutdownIncomplete 并保留存储所有权。recover 只查询/销毁原资源、校验原阶段并发布或记录 output_unavailable；不新建/启动原代码。verify 只读、返回有界证据建议；沙箱核查的人工 response 不能用自述结果取代销毁/原字节，使用 recover。其他 Python 写工具的 T-09 人工核查契约不变。

## 代价、替代与来源

代价是多一次稳定 exec ID 与更多可信控制程序/数据格式、POSIX/cgroup v2 首个 profile 和 PNG 子集限制。直接以 stdout JSON 作为执行真相、同 UID 共享可写控制文件、宿主 tar 解包、错误后再运行代码、用人工“applied”伪造清理均不采用。以后改变公共字段、权限/输入路径、profile 或数据版本需相应 ADR/案例/质量证据。

查阅 2026-10-14：[Docker Engine API](https://docs.docker.com/reference/api/engine/)、[v1.41](https://docs.docker.com/reference/api/engine/version/v1.41/)、[create](https://docs.docker.com/reference/cli/docker/container/create/)，实际 Engine/CLI 27.0.3 / Linux ARM64。能力与效果以 [本轮验证](../validation/t10a-runtime-sandbox.md) 为准，不从文档、协议 fixture 或旧核心 CI 推断完整沙箱/真实模型质量。

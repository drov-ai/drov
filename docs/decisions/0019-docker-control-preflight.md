# ADR-0019：Docker 控制预检与固定 profile

- 日期：2026-10-14（Asia/Shanghai）
- 状态：内部控制/预检子范围已实现；Runtime 审批执行与恢复仍未实现
- 关联：ADR-0016/0017/0018、T-10A、B-16/17/18

## 原因与决定

文件接收已验收，真实代码派发仍需要可重复核对的控制基础。先交付私有 `drov.sandbox._docker` / `_profile`，不导出未接通的 PythonSandbox/DockerSandbox/SandboxResult 或 callable 执行入口。构造/导入无 I/O；显式 open 只调用本地可信 CLI 的 info/version/image inspect，不创建容器、pull、build 或执行代码。固定 argv 构造与创建后/有效控制检查用于后续专用派发器；它们本身不授权派发。

当前 Docker profile 要求 POSIX 本地 Unix socket、Linux Engine、cgroup v2、API ≥ 1.41、内存/swap/CPU CFS/PID/seccomp/private cgroup namespace 能力及本机同架构 ARM64/AMD64 镜像。未满足即拒绝，不回退；实际支持平台仍按 T-10B 单独验收。执行镜像固定为 CPython 3.11；宿主核心 CPython 3.11–3.14 矩阵不变。CPU 用可表示的 100000µs period / 整数 quota，拒绝不能精确表达的配置；默认值仍由 engineering 维护。

独立 0700 临时 CLI 目录只放空 auths/proxies 与 0600 固定 seccomp 副本，子进程只显式传 PATH，并从该私有目录工作；不读取个人 Docker 配置或继承宿主凭据/代理环境。可信 CLI 是宿主提供的绝对可执行路径，拒绝其他用户可写文件；解析后内容 hash、端点、实际 Engine ID/version/API/architecture、解析的 image config ID、原 immutable reference、profile hash、限额与控制版本进入内部 binding v1。随机临时路径不进入指纹。**当前指纹尚未绑定到 Run、审批或恢复检查点**；该接点由下一批实现，不从本批指纹推断恢复能力。

预检中的 seccomp 能力不等于默认已受限：本机默认 unconfined，但每次 create 必须显式给固定 profile。创建后核对完整实际 profile 的 JSON 内容；Docker inspect 保存展开的 JSON 而非 CLI 路径。保留官方原文件字节 hash，同时允许 Docker 的无语义格式压缩，拒绝实际 syscall 策略改变。有效控制还需可信 runner 在执行模型代码前核对；本批仅固定代码探针验证，不建立模型执行器。

CLI stdout+stderr 合计最多 2MiB，边读边限、有限控制期限，失败不输出原始 CLI 诊断。控制 Task 持有真实 spawn/进程组/管道，取消或超限后杀掉并等待本次控制进程组、回收读写 Task；CLI 消失不作为容器销毁证据。预检 open/close 并发保留控制所有权，失败清理私有配置。容器的持久租约、并发计量与后端收尾仍待专用 Runtime 路径，不能以此替代。

## 替代方案与代价

- 直接包装旧可信镜像探针为模型工具：缺原计划审批、租约、安全输入/导出与结果发布，拒绝。
- 接受任意 tag/远程 daemon/cgroup v1 或退到弱控制：扩大首期兼容与失败语义，当前不采用；以后改变要求需新的兼容决定与真实证据。
- 预检运行容器探测能力：会在批准前创建代码资源，与既定契约冲突。open 只查能力；实际控制检查留在派发后的可信入口、本轮显式测试探针与 T-10B。

代价是首个 profile 比 Docker 所有可能平台更窄，并需在安装包内保留固定 profile/许可。文件原文从 `sandbox/docker/` 无修改复制到 `src/drov/sandbox/`；`.gitattributes` 防止 checkout 换行转换破坏原字节，安装检查校验两份一致、hash 与许可。历史 P4 fingerprint、公开 schema/DDL 和依赖锁不变。

## 来源、证据与影响

Docker 官方 [create 参数](https://docs.docker.com/reference/cli/docker/container/create/)、[info](https://docs.docker.com/reference/cli/docker/system/info/)、[seccomp](https://docs.docker.com/engine/security/seccomp/) 于 2026-10-14 查阅；本机实际 CLI/Engine 27.0.3 / Linux ARM64。文档支持机制存在，实际生效以 [本轮验证](../validation/t10a-docker-controls.md) 为准，Moby v27.0.3 profile 归属在 [THIRD_PARTY](../../THIRD_PARTY.md)。未执行任意模型代码、付费调用或部署，不改变 T-10A 整卡退出条件。

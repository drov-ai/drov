# T-10A 镜像与字节层的实际准备

这里维护分析镜像 recipe 与可信镜像探针；SDK 可调用的 PythonSandbox 实现在 src/drov/sandbox，由 Runtime 专用路径派发。完整 CSV/PNG 示例见 [sandbox_offline.py](../examples/sandbox_offline.py)，实际证据见 [本轮验证](../docs/validation/t10a-runtime-sandbox.md)。任务状态与 T-10B 剩余专项以 [progress](../docs/planning/progress.md) 为准；单独镜像探针不能替代 SDK 验收。

第三批已新增 SDK 内部 Docker 控制/预检和固定控制检查，见 [ADR-0019](../docs/decisions/0019-docker-control-preflight.md) 与 [实际验证](../docs/validation/t10a-docker-controls.md)。open 仅查 info/version/image，不创建代码容器。显式可信探针 `scripts/check_docker_backend.py` 检查标准库计算/有效控制/两次独立目录及归属清理；脚本不接收用户代码，不做 Runtime 审批/执行或文件导出。

```sh
uv run --locked python scripts/check_docker_backend.py \
  --docker /usr/local/bin/docker \
  --host unix:///Users/yanlong/.docker/run/docker.sock \
  --image sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45 \
  --work-directory .drov/my-control-probe
```

上面的 image ID 仅为本 checkout 的实际记录；换机器用自己已固定且预装的同架构镜像，目录必须新建。probe 创建前保存独立 fixture 台账，应答失败只查/清理原名称和标签，不创建替代资源；中断后保留台账并按记录恢复，不能选共享镜像批量清理。它不是生产执行租约或 SDK 崩溃恢复验收。

## 镜像锁与构建

[wheel 清单](analysis/wheels-linux-arm64.json) 和 [require-hashes 锁](analysis/requirements-linux-arm64.lock) 由 Linux ARM64 / CPython 3.11 实际解析生成，两者必须一致。这个平台锁不能用于 AMD64；T-10B 已另行实际下载核验 AMD64 wheel/哈希/许可，文件为 wheels-linux-amd64.json、requirements-linux-amd64.lock、license-inventory-linux-amd64.json。准备脚本默认 ARM64，显式 --platform linux/amd64 使用 AMD64 的固定 base/锁；镜像原生执行与远程 CI 状态见 [专项验证](../docs/validation/t10b-sandbox-faults.md)，下载本身不表示镜像通过。SDK 的 uv.lock 不含这些分析依赖，它们只在显式准备的镜像内安装。

构建工具从固定 PyPI wheel URL 下载并逐个核对 hash，输出私有构建目录、license inventory、日志和 `image.json`。构建阶段 `--network=none`，pip `--no-index --require-hashes` 使用预下载 wheel；不在代码执行期间安装包。镜像的 dist-info license/notice 原样保留。构建使用实际 Engine 27.0.3 可用的 legacy builder；未安装 buildx，不以此承诺其他 Engine 版本兼容。

```sh
# 在源码仓库根目录，显式提供可信 CLI 与本地 Unix socket。
uv run --locked python scripts/prepare_analysis_image.py \
  --docker /usr/local/bin/docker \
  --host unix:///Users/yanlong/.docker/run/docker.sock \
  --work-directory .drov/t10a-analysis-image \
  --curl /usr/bin/curl
```

`--curl` 可省略而使用标准库 HTTPS；本机 Python TLS 曾连接失败，实际用系统 curl 成功下载。curl 禁用个人配置，只沿已有网络代理/CA 配置连接公开 PyPI，不打印配置值，不关闭 TLS 校验；仅允许 HTTPS，禁止跟随重定向，校验固定字节 hash。Docker 控制面另用空 auths/proxies 私有配置和有限环境，不继承上述代理注入。脚本不推送镜像、修改 daemon 或调用模型。

本地构建得到 `sha256:<image-config-id>`，`RepoDigests=[]` 是正常事实；不能把该 ID 改写成 registry manifest digest。运行探针用实际 ID 和 `--pull=never`。不同构建时间/工具可能得到不同 ID，部署者须检查自己的实际构建记录，不能仅凭 recipe 声称 bit-for-bit 重建成功。

## 真实镜像探针

```sh
uv run --locked python scripts/check_analysis_image.py \
  --docker /usr/local/bin/docker \
  --host unix:///Users/yanlong/.docker/run/docker.sock \
  --image-record .drov/t10a-analysis-image/image.json \
  --work-directory .drov/t10a-analysis-probe
```

固定执行者源码读取合成 CSV，实际运行 numpy/pandas/matplotlib，生成并解码检查 CSV/PNG；固定非 root、no-network、有限 cgroup/tmpfs、只读 root、显式 seccomp 等配置，并核对有效值。只清理具有本轮唯一身份与标签的容器；不停止用户 daemon。容器销毁后用字节层暂存输出、关闭并重新打开 SQLite/store 后按 hash 读回。

这个探针使用可信固定代码和测试传输，不是安全导出器：没有 Run 授予、输入只读保护、审批、工具结果、动态 Authorizer、ready 发布或授权下载。不得用它代替任务卡闭环，也不得用它执行任意模型代码。这些隔离/资源/子进程/RPC/SDK硬终止的实际后端证据由T-10B专项给出；不能归到这个独立探针。

## 私有字节层

`from drov.artifacts import ArtifactInput, ArtifactRef, ArtifactInfo, ArtifactLimits, LocalArtifacts` 已实现。Runtime 装配，start/run/stream 接收输入，get/read_artifact 授权返回 ready 输入/产物，见 [文件通道示例](../examples/artifact_inputs_offline.py) 与 ADR-0018。代码执行后有界安全导出、PNG 校验、可靠暂存、确认整个容器销毁，再同一事务发布原结果与 ready 产物，见 ADR-0020。独立字节层仍可显式 open(storage=已打开的 SQLiteStorage)/aclose；私有阶段方法不产生 ready 元数据，应用不得用它们绕过授权。

## 真实 SDK 示例

以下为本 checkout 的实际本地分析 image ID；换机器须使用自己的固定构建记录。每次 directory 选新的私有目录，原库/字节/台账保留用于检查，不共用容器状态：

```sh
uv run --locked python examples/sandbox_offline.py \
  --docker /usr/local/bin/docker \
  --host unix:///Users/yanlong/.docker/run/docker.sock \
  --image sha256:90b64b063ac57fd1399adb4447f77718ef839d9a1cb61a0bb1df93dadaf29f45 \
  --directory .drov/my-sdk-analysis --chart
```

加 --stream 检查流入口；不加 --chart 可用预装固定标准库镜像。示例自己通过默认审批后检查工具真实结果，模型是确定性夹具，不能作为真实模型质量。默认输入/输出额、PNG 有界解码 profile 与 POSIX 限制见工程方案第 9 节；合法但超出 PNG profile 的文件也会被拒绝。T-10B 的受测隔离/资源/硬终止与 Linux3.11–3.14原生AMD64专项CI/安装已完整通过，见专项验证；完整维护/备份仍T-15，不用于敌对公网多租户部署承诺。

当前字节层仅在 POSIX 控制下可打开；构造/导入在其他平台仍无 I/O。文件目录、store.json、stage.json 与原始 ID 字节共同构成配对数据；完整备份必须使用 T-15 已实现的成对备份/恢复屏障，见[数据维护](../docs/guide/maintenance.md)。无清单目录、临时清单或其他未知项使 open fail closed，保留证据，不手工删除/改写后假装恢复已验收。调小容量不会清空旧预留；容量不足明确拒绝。

[许可 inventory](analysis/license-inventory-linux-arm64.json) 记录实际 wheel 中的声明和 license 文件 hash；依赖源码归属及 Moby profile 来源见 [THIRD_PARTY](../THIRD_PARTY.md)。安全 profile 是未修改的上游 v27.0.3 文件，不是 Drov 原创，也不是漏洞扫描证明。

T-10B 新增真实隔离/资源/应答故障/SDK 与 stream SIGKILL 测试和 Docker 专项 CI。复跑、实际通过/失败和收尾限制见 [专项验证](../docs/validation/t10b-sandbox-faults.md)；不以工作流存在推断通过。

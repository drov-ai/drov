# T-10A 镜像与字节层的实际准备

这里包含分析镜像 recipe 与可信镜像探针，**不是可调用的 PythonSandbox，也不是任意模型代码执行器**。T-10A 尚未完整验收；状态与剩余项见 [progress](../docs/planning/progress.md)，实际结果见 [本轮验证](../docs/validation/t10a-artifact-staging.md)。

## 镜像锁与构建

[wheel 清单](analysis/wheels-linux-arm64.json) 和 [require-hashes 锁](analysis/requirements-linux-arm64.lock) 由 Linux ARM64 / CPython 3.11 实际解析生成，两者必须一致。这个平台锁不能用于 AMD64；Linux Docker CI 的其他架构镜像尚待 T-10B。SDK 的 uv.lock 不含这些分析依赖，它们只在显式准备的镜像内安装。

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

这个探针使用可信固定代码和测试传输，不是安全导出器：没有 Run 授予、输入只读保护、审批、工具结果、动态 Authorizer、ready 发布或授权下载。不得用它代替任务卡闭环，也不得用它执行任意模型代码。恶意代码隔离、资源超限、遗留子进程、RPC 应答丢失及 SDK 硬终止恢复仍待实际后端与 T-10B。

## 私有字节层

`from drov.artifacts import ArtifactInput, ArtifactRef, ArtifactInfo, ArtifactLimits, LocalArtifacts` 已实现值类型及存储。LocalArtifacts 目前需显式 `await store.open(storage=已打开的 SQLiteStorage)`，关闭用 `aclose()`；`_reserve/_stage/_read_staged/_pending` 是内核接点，应用不得把它们包装成绕过授权的上传/下载 API。它们不会产生 ready 元数据，Runtime 的 artifacts 参数仍不可用。

当前字节层仅在 POSIX 控制下可打开；构造/导入在其他平台仍无 I/O。文件目录、store.json、stage.json 与原始 ID 字节共同构成配对数据；不能在尚未完成 T-15 备份 barrier 时声称有完整 Artifact 备份。无清单目录、临时清单或其他未知项使 open fail closed，保留证据，不手工删除/改写后假装恢复已验收。调小容量不会清空旧预留；容量不足明确拒绝。

[许可 inventory](analysis/license-inventory-linux-arm64.json) 记录实际 wheel 中的声明和 license 文件 hash；依赖源码归属及 Moby profile 来源见 [THIRD_PARTY](../THIRD_PARTY.md)。安全 profile 是未修改的上游 v27.0.3 文件，不是 Drov 原创，也不是漏洞扫描证明。

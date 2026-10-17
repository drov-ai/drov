# Python 沙箱与文件产物

## 选择后端与镜像

| 路径 | 实际支持 | 需要准备 |
| --- | --- | --- |
| Docker 标准库 | 单次 Linux Python，CSV 读写与文本；本地 POSIX 宿主 | 可信 CLI、Unix socket、Linux Engine/cgroup v2、固定同架构镜像 |
| Docker 分析镜像 | 标准库加 numpy/pandas/matplotlib，受支持 PNG | 原生 Linux ARM64/AMD64 的固定镜像与平台锁 |
| 可选 E2B | T-10C 受测固定 template/build 的单次代码/文件协议 | e2b extra、账号/Key、明确上传授权与独立累计费用预算 |

当前没有持久 Notebook、共享工作区、自动安装库或任意网络访问。Docker 和 E2B 的保证分别验证，不能相互推断。

## 准备本地 Docker

先阅读[环境与分析镜像说明](../../sandbox/README.md)，使用其中记录的固定基础 manifest、平台 requirements 锁和许可 inventory。基础与分析镜像分别固定；宿主 image ID、CLI、socket 必须在自己的机器重新核实，不复制其他人的绝对路径或 image ID。

`scripts/prepare_analysis_image.py --help` 说明显式构建参数；`scripts/check_analysis_image.py --help` 和 `scripts/check_docker_backend.py --help` 提供本地检查。正常 SDK 执行不会拉取/构建镜像，配置缺失或不匹配直接拒绝。Docker daemon 属于可信控制面，不把宿主凭据挂入容器。

准备好后在仓库根目录设置三个**非秘密的本机配置值**，每次 directory 使用新目录：

```sh
export DROV_DOC_DOCKER=/absolute/path/to/docker
export DROV_DOC_HOST=unix:///absolute/path/to/docker.sock
export DROV_DOC_IMAGE=sha256:your_verified_analysis_image_id
uv run --locked python examples/sandbox_offline.py \
  --docker "$DROV_DOC_DOCKER" --host "$DROV_DOC_HOST" \
  --image "$DROV_DOC_IMAGE" --directory .drov/docs-analysis-01 --chart
uv run --locked python examples/sandbox_offline.py \
  --docker "$DROV_DOC_DOCKER" --host "$DROV_DOC_HOST" \
  --image "$DROV_DOC_IMAGE" --directory .drov/docs-analysis-02 --chart --stream
```

这些 export 值是需要替换的示意，不是开箱可用的镜像。标准库镜像运行同一例子时去掉 `--chart`，并使用另一个新目录。

输入为 `value\n12\n18\n30\n`。默认审批检查原 code/inputs/outputs 后执行；预期 stdout 为 60，summary.csv 为 count=3、sum=60、mean=20，分析镜像另生成 400×300 PNG。示例关闭 Runtime 后重新打开授权读取并核对 hash；查看和下载不会再执行代码。代码由夹具预设，不能据此宣称任意数据分析质量。

## 文件授权与容量

宿主传入 `ArtifactInput` 或显式授予的 `ArtifactRef`；不从逻辑文件名寻址任意宿主文件。每个 Run 的授予固定，其他 Run 不会自动继承。输入/输出名称属于正文；元数据 read 与名称/字节 view_content 分开，跨 scope 猜 ID 不泄露存在性。未 ready 暂存不能下载。

LocalArtifacts 与 SQLite 配对，目录/清单/字节共同保存。输入、输出、未完成预留、未消费上传和清单开销都占容量；容量不是底层磁盘硬限额。具体默认值唯一维护在[工程方案第 9 节](../planning/engineering.md#9-首版-docker-代码与文件沙箱)，类型和可配置值见[生成 schema](../reference/schema-v1.json)。调低容量不自动清空旧预留。

导出只接受受控普通文件名、大小/数量/格式；链接、路径穿越、超大内容与不支持的 PNG profile 会拒绝。代码完成后先可靠暂存、确认整个环境销毁，再共同发布 ready 引用和原工具结果；清理不明时保留租约，恢复查询原环境，不重跑原代码。

## 隔离与维护

Docker profile 固定无网络、无挂载/设备/socket、非 root、只读根、受限 tmpfs、资源与 PID 限额、cap-drop/no-new-privileges 和有效 seccomp。受测故障证据见[T-10B](../validation/t10b-sandbox-faults.md)。这些控制不证明抵抗所有内核漏洞；首版面向受管理本地应用，不承诺敌对公网多租户强隔离。

环境销毁不删除已保存文件。默认保留至显式维护；forget、Session 逻辑删除、字节回收和供应商物理擦除是不同操作。备份必须包含配对 SQLite/字节与 ready 校验，恢复副本有屏障，见[数据维护](maintenance.md)。

## 可选 E2B：独立费用与上传边界

E2B 需显式 template/build、受测 profile、凭据引用和完整寿命累计费用预留；不把免费额度或本地预算当作账户实付余额。Key 只在宿主安全环境配置 `E2B_API_KEY`，不进入模型、沙箱或日志。调用前明确合成/业务资料允许上传的范围、服务数据保留条件、沙箱总预算和必要清理预留；与模型预算分开。

区域是项目声明，当前 SDK 不能证明驻留，要求强区域保证时拒绝。远端销毁是资源事实，不保证供应商副本擦除。实际 template/SDK/隔离/故障/费用证据见[T-10C](../validation/t10c-e2b.md)与[ADR-0023](../decisions/0023-e2b-single-execution.md)。本轮文档验证不创建云资源；没有 E2B 账号不影响 Docker 路径。

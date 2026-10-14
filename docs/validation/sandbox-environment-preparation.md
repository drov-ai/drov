# T-10A 前的本地 Docker 与镜像准备

- 日期：2026-10-14（Asia/Shanghai）。起点 main / 0f794f6，工作区干净且与 origin/main 同步。
- 用户说明已开启 Docker Desktop；本轮完成资源准备，没有实施 Drov 沙箱处理器、公共 API 或恢复租约，也没有关闭 T-10A/T-10B。
- 前一轮 daemon 无法连接是历史事实；本轮实际检查已可连接 `desktop-linux`，不覆盖旧验证记录。

## 实际准备结果

| 资源 | 实际核实值 |
| --- | --- |
| CLI / Engine | Docker 27.0.3，API 1.46；Docker Desktop 4.32.0 (157355) |
| Linux 后端 | linux/arm64，Kernel 6.6.32-linuxkit；10 CPU、8,221,618,176 bytes 内存 |
| 官方镜像来源 | docker.io/library/python，选择 3.11.17-slim-bookworm；标准库计算，不安装业务依赖 |
| 本地 ARM64 固定镜像 | `docker.io/library/python@sha256:dabc8c628ee25179a9fcb8ab81e592938ee2bad3ffb15e64157f8fe428d5279f`，linux/arm64/v8 |
| 本地 image ID / 大小 | `sha256:3c50954f1f466ff97efcbe7678f38adf0401eb3ddda4966e04f5be9cd6c71aed`；162,569,193 bytes |
| 多平台 index（来源记录） | `sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89`；与平台 manifest/image config ID 区分 |
| 镜像源码 | docker-library/python commit `cede844ace77284e32c03b61ebc35cdfc945e862` 的 3.11/slim-bookworm/Dockerfile |
| 本地准备快照 | `.drov/sandbox-environment.json`，仅非秘密配置/版本/hash/检查结果；不是尚未实现的 Drov 配置 DTO |

准备时显式下载官方固定 manifest；实际运行使用 `--pull=never`，没有自动换标签或现场安装依赖。镜像 Config.Volumes/Healthcheck 均为空；环境变量名称仅 PATH/LANG/GPG_KEY/PYTHON_VERSION/PYTHON_SHA256（GPG_KEY 是镜像公开构建公钥，未读取宿主凭据）。不将固定 digest 等同漏洞扫描或安全认证。

## 实际容器检查与发现

创建受控临时容器：禁联网、不挂宿主文件/目录/socket、不发布端口、rootfs 只读、UID/GID 65534、cap-drop=ALL、no-new-privileges、日志 none、512MiB memory+swap 总额、1 CPU、64 PID、/work 64MiB tmpfs、/tmp 16MiB tmpfs、/dev/shm 16MiB。运行的是执行者自有的小型环境探针，不是模型生成代码。

首次检查发现 daemon SecurityOptions 为 `seccomp,profile=unconfined`，容器 `/proc/self/status` 的 Seccomp=0；没有把这一配置判为合格，也没有修改 daemon 全局设置。采用 **Moby v27.0.3 官方默认 seccomp JSON** 显式配置到测试容器，再核实 Seccomp=2、NoNewPrivs=1、CapEff=0。配置下载到 `.drov/docker-sandbox-cli/seccomp-v27.0.3.json`，SHA-256 为 `9c1025c88ccaa517b648da571961838744ea2137f176bfe6a48b21294cae9c76`；Apache-2.0 LICENSE 同目录保存。

实际容器返回 Python 3.11.17/Linux/UID 65534；cgroup v2 的 memory.max=536870912、memory.swap.max=0、cpu.max=`100000 100000`、pids.max=64。根目录写入返回 EROFS，/work 可写。network=none，无 IPv4 路由；内核仍显示无路由的虚拟接口，不能用“只能看到 lo”作为通过条件。没有进行宿主网络哨兵连接或 IPv6 越界专项，完整网络隔离仍待 T-10B。

控制 CLI 使用独立空 auths/proxies 的配置目录、显式本地 socket 与有限环境；未读取或复制用户 Docker config.json。Docker 官方说明默认 CLI 代理设置可注入新容器，准备探针实际核对容器无额外环境变量名称。T-10A 的真实控制器必须保持这一边界与有效 seccomp，不能因为 daemon 当前 unconfined 就静默降级。

每次测试仅控制标有 `dev.drov.fixture=environment-preparation` 的本轮自建容器。成功观察 exited/ExitCode=0 后删除；失败探针也在 finally 删除。最终按该标签查询残留数量 0；未操作用户已有容器、镜像或全局设置。

## 命令与来源

```sh
docker version --format '{{json .}}'
docker info --format '{{json .OSType}} {{json .Architecture}} {{json .ServerVersion}} {{json .NCPU}} {{json .MemTotal}}'
docker buildx imagetools inspect docker.io/library/python:3.11-slim-bookworm
docker pull --platform linux/arm64/v8 docker.io/library/python@sha256:dabc8c628ee25179a9fcb8ab81e592938ee2bad3ffb15e64157f8fe428d5279f
.drov/tooling/bin/uv run --locked python .drov/prepare-sandbox-environment.py
```

本地脚本只是本轮准备工具，忽略目录内保留以便复核；不是 SDK、永久测试套件或生产沙箱。下载日志 .drov/sandbox-prep-pull.log、registry 原始 index .drov/sandbox-image-index.json、最终探针 .drov/sandbox-environment-probe.json 和完整快照 .drov/sandbox-environment.json 均已实际生成。文档相对链接/AST/Git 差异检查另记录；本轮无 SDK/测试/锁/schema 变化，无须把前一轮全库成绩当作新沙箱验收。

查阅日期均为 2026-10-14，来源为 [Docker Official Python](https://hub.docker.com/_/python)、[固定源码 Dockerfile](https://github.com/docker-library/python/blob/cede844ace77284e32c03b61ebc35cdfc945e862/3.11/slim-bookworm/Dockerfile)、[digest 说明](https://docs.docker.com/dhi/explore/security-concepts/digests/)、[CLI 代理行为](https://docs.docker.com/engine/cli/proxy/)、[seccomp 配置](https://docs.docker.com/engine/security/seccomp/) 和 [Moby v27.0.3 默认配置](https://github.com/moby/moby/blob/v27.0.3/profiles/seccomp/default.json)。

来源许可分别记录：Dockerfile 仓库 [MIT](https://github.com/docker-library/python/blob/cede844ace77284e32c03b61ebc35cdfc945e862/LICENSE)、Python 3.11.17 [PSF 与所含组件许可](https://docs.python.org/3.11/license.html)、Moby v27.0.3 [Apache-2.0](https://github.com/moby/moby/blob/v27.0.3/LICENSE)；Debian/镜像其他组件保留其各自许可。当前仅本地使用官方预构建镜像和准备用配置，没有复制进 SDK 或分发镜像；T-10A 若正式打包 profile，须同步第三方声明/许可并固定内容 hash。

## 完成界限与接续

资源已准备到能开展 T-10A：用户保持 Docker Desktop 开启即可，无需自己计算 digest、购买云账号或配置模型 Key。新会话先复查 daemon、上述本地镜像和非秘密快照，并读取 ADR-0013 与工程/运行契约。下一项仍为 T-10A 的真实 SDK 沙箱闭环。

这次只确认简单代码可在指定限制下运行与基础配置实际生效；尚未验证 OOM/CPU 压力/PID 风暴、磁盘/输出上限、宿主/用户隔离攻击、取消/进程树清理、丢应答/硬崩溃/恢复，也未完成 Linux CI 或 macOS 平台支持承诺。上述分别按 T-10A/T-10B 验收，不把本次准备当成沙箱完成。

本轮文档检查：50 个 Markdown、401 个本地链接、14 个 Python 片段 AST，错误 0；Git 差异检查通过。仅更新工程说明、progress 与本验证记录，以文档提交保存并同步；无 SDK/测试/依赖/品牌变更，未调用模型或发布/部署。

2026-10-14 后续范围补记：用户按 [ADR-0016](../decisions/0016-sandbox-artifacts-and-backends.md) 纳入受控文件/持久 CSV/PNG 产物。上面的实际标准库镜像与探针成绩保持不变；该镜像没有 numpy/pandas/matplotlib，不能用于证明新增分析/产物验收。T-10A 执行者按工程方案另行构建并固定数据分析镜像、核实文件/私有存储与真实闭环；本准备记录不关闭任何沙箱任务。

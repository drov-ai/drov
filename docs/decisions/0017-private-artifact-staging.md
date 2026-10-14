# ADR-0017：私有字节暂存与本地构建镜像的固定身份

日期：2026-10-14（Asia/Shanghai）。状态：T-10A 首批已实施，验证见 [字节层与镜像记录](../validation/t10a-artifact-staging.md)。**不是 T-10A 整卡验收，尚无 PythonSandbox/Run 文件接收/ready 发布/授权下载。** ADR-0016 的最终事务、授权、隔离和单次派发规则继续适用。

## 原因

固定分析镜像需要实际构建；本地 Docker build 给出的是不可变 image config ID，未推送镜像没有 repository manifest digest，不能伪造 `repository@sha256`。文件字节必须在接收/派发事务之外可靠暂存，先落实有界私有存储，才能接入输入接收和结果原子发布。

## 决定

1. Docker 后端的待实现配置允许显式 `repository@sha256:<manifest>` 或本地 `sha256:<image-config-id>`，都必须已预装、inspect 匹配固定身份，不接受可变 tag、不自动拉取。本地 config ID、平台 manifest digest、多平台 index digest 分别记录。当前仅镜像准备脚本/探针使用该 ID；未创建公共 DockerSandbox 符号。
2. `drov.artifacts` 的值类型和 LocalArtifacts 私有字节层先落地。构造无 I/O；`open(storage=已打开的 SQLiteStorage)` 获取排他锁，核对 POSIX 0700/0600、所有者/链接及配对。SQLite 的既有 metadata 增量键 `artifact_store_id` 与字节目录 store ID 双向绑定；目录还保存由数据库内部去重身份单向派生的绑定 hash，不保存原身份。records DDL、schema/record 版本仍为 1，旧数据库无需迁移或回填文件事实；以后支持更广平台须实际核对等效访问控制。
3. 内核私有 `_reserve` 先可靠提交带 tenant/user/Agent/来源的文件台账；排他锁＋串行门保护容量，字节写入、fsync、目录同步之后才标 `staged`。每批为有界清单额外占 16KiB 逻辑配额，空文件/空批也占额；原始字节占用与未完成预留跨重开保留。这个逻辑配额不是文件系统的物理 block/inode 配额。stage 只表示字节暂存，**不能作为 ready Artifact 元数据或成功工具结果**。
4. ID 生成宿主路径，逻辑名只做清单数据；目录描述符锚定、O_NOFOLLOW/O_NONBLOCK、文件类型/权限/单链接/长度/hash/读前后状态复核防止错误路径或无界读取。拒绝输入规范化重名和有界输出路径中的逃逸。后端安全导出、PNG 解码、Authorizer 和 Run 授予尚未接入，不能从该字节层推断它们已经可用。
5. 取消调用方后等待实际磁盘 worker 停止才释放门/锁；失败台账和部分字节保留，不自动删除或重新写入已开始的批次。中断留下无清单目录/临时清单时 fail closed，后续恢复卡必须显式查证，不能重新按空目录计量。本批不新增公开上传/下载/维护接口；Runtime 装配、SQLite Artifact 元数据/Run 授予、去重、ready 与原工具结果的共同提交、租约恢复仍在同一 T-10A 内完成。

## 替代方案与代价

- 先推送分析镜像获取 manifest digest：引入目前不需要的注册服务与公开/私有发布身份；选用实际本地不可变 ID，未发布镜像。
- 把文件内容塞进 records 或工具 JSON：扩大存储/上下文、无法满足已选私有字节边界；继续采用字节暂存与事务元数据分离。
- 无台账直接写字节、启动时删残留：可能丢恢复证据或重置容量；选择保守保留，代价是后续需要受控恢复/回收，首批未交付这些控制入口。

## 影响

API 文件接口/工程方案补充本地固定 ID、字节层真实入口与批次配额；生成 ArtifactRef/Info/Limits schema，ArtifactInput 是宿主 bytes 输入，不生成 JSON 上传协议。元数据配对增量保持旧记录兼容；最终授权与结果事务要求不降级。源码/sdist 含镜像 recipe、ARM64 wheel 锁/许可清单、未修改的 Moby v27.0.3 seccomp profile 与原许可。SDK wheel 不安装分析库、不连接 Docker。历史 P4 fingerprint、uv.lock、品牌、T-10 证据不变。

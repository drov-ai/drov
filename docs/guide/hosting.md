# 构建与托管文档

## 本地构建

```sh
uv sync --locked --group docs
uv run --locked --group docs python scripts/build_docs.py
uv run --locked --group docs python scripts/check_docs.py
uv run --locked --group docs python -m http.server 8000 --bind 127.0.0.1 --directory .drov/site
```

在本机打开 `http://127.0.0.1:8000`。构建输出为 `.drov/site/`，压缩构件为 `dist/drov-docs-0.1.0a0.zip`，摘要在 `dist/drov-docs-0.1.0a0.sha256`。最后一条只启动本地预览，Ctrl-C 停止。

构建器只把明确允许的用户文档、社区说明和生成 schema 放进暂存目录，严格构建 MkDocs 并检查产物内的链接/锚点。规划、评测原始 JSON、私有 `.drov`、凭据和品牌原件不进入输出。指向未内置的源码/证据链接指向构建时精确 Git 提交；无 Git 的 sdist 构建把 source_commit 记为 null，单列 SDK 源码链接基准并标记 source_archive，不据此声称整个源码包来自该提交。构建 manifest 记录实际输入 hash，不能只凭版本号断言字节相同。

`mkdocs.yml` 和自有主题在仓库中，MkDocs 及其依赖只属于 docs group，不成为 SDK 必装依赖。主题使用系统字体和本地 CSS/JS，无外部字体、统计或第三方运行时。当前使用文本 Drov 字标，忽略目录中的品牌素材不复制进站点；公开前仍需确认原品牌来源与使用范围。

SDK 公共签名改变时，用同一构建命令加 `--update-signatures` 显式重生成并提交签名页；普通构建只校验，不改写仓库源文档。

## Cloudflare Pages 条件

本轮只产出可预览静态文件，不创建 Cloudflare 项目、上传站点、绑定域名或部署 Runtime。准备公开时先具备：现有仓库读权限、Cloudflare Pages/DNS 管理范围、最终域名拼写与品牌授权，以及明确的部署授权。

仓库记录的用户域名为 `drop.dev`，尚未确认是否最终使用，不自动改成其他域名。文档未配置 canonical 域名，输出可在根目录或子路径预览。

在配有 Python 3.11+ 与 uv 0.13.0 的构建环境，使用上述锁定同步和 `scripts/build_docs.py`，发布目录 `.drov/site`。也可在已验证本机/CI 构建后解压 zip 供 Pages 直接上传。不同托管环境的运行时/依赖安装仍需实际验证；本地构建成功不等于 Pages 远端构建或上线成功。

首次公开由 [T-18](../planning/implementation-plan.md) 核对 Tag、wheel/sdist、完整安装/质量门槛和文档构件；发布/回滚顺序唯一维护在[发布策略](../planning/release-policy.md#5-发布与官网)。站点回退选择原已验证构件，Python 包用新版本修复，不覆盖同版本。

构建机制依据 [MkDocs 配置](https://www.mkdocs.org/user-guide/configuration/)与[主题接口](https://www.mkdocs.org/dev-guide/themes/)（查阅 2026-10-17，实际锁定 MkDocs 1.6.1）。

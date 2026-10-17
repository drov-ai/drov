"""Build only allowlisted public documentation; no credentials, deployment or cloud calls."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / ".drov/docs-source"
OUTPUT = ROOT / ".drov/site"
# SDK checkpoint retained for sdist builds without Git. Not a package release tag.
ARCHIVE_SOURCE = "0aa0168572f7994326e84ab66e53a0a83fae6132"
GUIDES = (
    ("index", "文档概览"),
    ("quickstart", "快速入门"),
    ("concepts", "运行原理"),
    ("examples", "三个参考应用"),
    ("api", "Python API"),
    ("api-signatures", "实际签名"),
    ("integrations", "模型与 MCP"),
    ("server", "HTTP 与工作台"),
    ("sandbox", "Python 沙箱与文件"),
    ("maintenance", "数据维护"),
    ("troubleshooting", "排错"),
    ("limits", "实际支持范围"),
    ("release-notes", "版本说明"),
    ("hosting", "构建与托管"),
)
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^\s)]+)\)")


def version() -> str:
    tree = ast.parse((ROOT / "src/drov/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"[0-9A-Za-z.]+", value):
                return value
    raise RuntimeError("Invalid package version")


def source_state() -> tuple[str, bool | None]:
    # A parent repository must not be mistaken for the sdist's own checkout.
    if not (ROOT / ".git").exists():
        return ARCHIVE_SOURCE, None
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    return commit, dirty


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_markdown(source: Path, destination: Path, mapping: dict[Path, Path], commit: str) -> None:
    content = source.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        target = urlsplit(match.group(1))
        if target.scheme or target.netloc or not target.path:
            return match.group(0)
        local = (source.parent / unquote(target.path)).resolve()
        if not local.is_relative_to(ROOT) or (not local.is_file() and local not in mapping):
            raise RuntimeError(f"Missing/outside documentation link in {source.relative_to(ROOT)}")
        if local in mapping:
            path = posixpath.relpath(mapping[local].as_posix(), destination.parent.as_posix())
            url = quote(path, safe="/.")
        else:
            url = "https://github.com/drov-ai/drov/blob/" + commit + "/"
            url += quote(local.relative_to(ROOT).as_posix(), safe="/.")
        if target.query:
            url += "?" + target.query
        if target.fragment:
            url += "#" + target.fragment
        return match.group(0).replace(match.group(1), url)

    content = LINK.sub(replace, content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def signature_page() -> str:
    # AST reads the actual source, without importing adapters or resolving private defaults.
    sources = {
        "Agent": "src/drov/agent.py",
        "Runtime": "src/drov/runtime/core.py",
        "SQLiteMaintenance": "src/drov/storage/maintenance.py",
    }
    lines = [
        "# 实际 Python 签名",
        "",
        "由构建器从本快照源码生成，参数与默认值未经手工抄写。"
        "`self` 为实例参数；星号后的参数仅接受关键字。内部辅助方法不列为用户 API。",
        "",
    ]
    for name, file in sources.items():
        source = ROOT / file
        source_text = source.read_text()
        source_lines = source_text.splitlines()
        lines += [f"## {name}", "", f"[源码](../../{file})", "", "```python"]
        tree = ast.parse(source_text)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != name:
                continue
            for method in node.body:
                if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if method.name.startswith("_") and method.name != "__init__":
                    continue
                for decorator in method.decorator_list:
                    lines += ["@" + ast.unparse(decorator)]
                signature = textwrap.dedent(
                    "\n".join(source_lines[method.lineno - 1 : method.body[0].lineno - 1])
                ).rstrip()
                if not signature.endswith(":"):
                    raise RuntimeError("Cannot extract source signature")
                lines += [signature + " ..."]
        lines += ["```", ""]
    return "\n".join(lines)


def prepare() -> tuple[str, dict[str, object]]:
    release = version()
    commit, dirty = source_state()
    mapping = {ROOT / "docs/index.md": STAGING / "index.md"}
    for name, _ in GUIDES:
        mapping[ROOT / f"docs/guide/{name}.md"] = STAGING / release / f"{name}.md"
    if (ROOT / "docs/guide/api-signatures.md").read_text() != signature_page():
        raise RuntimeError("API signatures changed; regenerate docs/guide/api-signatures.md")
    for source_name, target in (
        ("CONTRIBUTING.md", "contributing.md"),
        ("SECURITY.md", "security.md"),
        ("THIRD_PARTY.md", "third-party.md"),
    ):
        mapping[ROOT / source_name] = STAGING / "community" / target
    for name in ("schema-v1.json", "openapi-v1.json"):
        mapping[ROOT / "docs/reference" / name] = STAGING / release / "reference" / name
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    for source, destination in mapping.items():
        if source.suffix == ".md":
            copy_markdown(source, destination, mapping, commit)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    license_page = STAGING / "community/license.md"
    license_page.write_text(
        "# 许可与来源\n\nDrov 自有代码/文档采用 Apache-2.0。品牌素材没有据此重新许可。"
        "[第三方来源](third-party.md)另列依赖与声明。\n\n```text\n"
        + (ROOT / "LICENSE").read_text()
        + "\n```\n",
        encoding="utf-8",
    )
    (STAGING / "versions.md").write_text(
        "# 文档版本\n\n"
        f"- [{release} 开发快照]({release}/index.md)：唯一现有版本，尚未公开发包。\n\n"
        "本地构建的 [manifest](build-manifest.json) 绑定输入 hash 与源码提交，"
        "不是正式发布记录。后续公开版本由发布流程冻结并保留原快照。\n",
        encoding="utf-8",
    )
    inputs = set(mapping) | {ROOT / "LICENSE", ROOT / "mkdocs.yml", ROOT / "pyproject.toml"}
    inputs |= set((ROOT / "docs/theme").rglob("*"))
    inputs |= {ROOT / "scripts/build_docs.py", ROOT / "scripts/check_docs.py", ROOT / "uv.lock"}
    # Source identity includes the SDK; no runtime database or environment is opened.
    inputs |= set((ROOT / "src/drov").rglob("*.py"))
    manifest: dict[str, object] = {
        "format": 1,
        "package_version": release,
        "source_commit": commit,
        "working_tree_dirty": dirty,
        "source_kind": "source_archive" if dirty is None else "git_checkout",
        "public_deployment": False,
        "brand_assets_included": False,
        "inputs": {
            path.relative_to(ROOT).as_posix(): digest(path)
            for path in sorted(inputs)
            if path.is_file()
        },
    }
    (STAGING / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return release, manifest


def main() -> None:
    from mkdocs.commands.build import build
    from mkdocs.config import load_config

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    os.chdir(ROOT)
    release, manifest = prepare()
    nav: list[dict[str, object]] = [
        {"首页": "index.md"},
        {release: [{label: f"{release}/{name}.md"} for name, label in GUIDES]},
        {
            "社区": [
                {"贡献指南": "community/contributing.md"},
                {"安全报告": "community/security.md"},
                {"许可": "community/license.md"},
                {"第三方来源": "community/third-party.md"},
            ]
        },
        {"版本": "versions.md"},
    ]
    config = load_config(
        config_file=str(ROOT / "mkdocs.yml"),
        nav=nav,
        extra={
            "version": release,
            "version_home": release + "/",
            "api_page": release + "/api/",
        },
    )
    build(config)
    # The custom client reads only the JSON index. Do not ship unused upstream JS.
    for name in ("lunr.js", "main.js", "worker.js"):
        (OUTPUT / "search" / name).unlink()
    sitemap = OUTPUT / "sitemap.xml"
    (OUTPUT / "sitemap.xml.gz").write_bytes(gzip.compress(sitemap.read_bytes(), mtime=0))
    subprocess.run([sys.executable, str(ROOT / "scripts/check_docs.py")], check=True)
    # ZIP entries use a fixed clock and permission bits for identical-input rebuilds.
    archive = ROOT / "dist" / f"drov-docs-{release}.zip"
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(OUTPUT).as_posix(), (2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                bundle.writestr(entry, path.read_bytes())
    archive.with_suffix(".sha256").write_text(f"{digest(archive)}  {archive.name}\n")
    print(
        json.dumps(
            {
                "version": release,
                "source_commit": manifest["source_commit"],
                "working_tree_dirty": manifest["working_tree_dirty"],
                "output": str(OUTPUT),
                "archive": str(archive),
                "sha256": digest(archive),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

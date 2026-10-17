"""Verify static documentation links, anchors and publication inventory without network calls."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / ".drov/site"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            identifier = values["id"]
            assert identifier is not None
            if identifier in self.ids:
                raise RuntimeError("Duplicate HTML ID")
            self.ids.add(identifier)
        for name in ("href", "src"):
            if values.get(name):
                value = values[name]
                assert value is not None
                self.links.append(value)


def check() -> dict[str, int]:
    pages: dict[Path, Page] = {}
    for path in SITE.rglob("*.html"):
        page = Page()
        page.feed(path.read_text(encoding="utf-8"))
        pages[path.resolve()] = page
    if not pages:
        raise RuntimeError("No built site")
    links = 0
    for source, page in pages.items():
        for href in page.links:
            target = urlsplit(href)
            if target.scheme in ("https", "mailto") or target.netloc:
                continue
            if target.scheme or target.path.startswith("/"):
                raise RuntimeError(f"Non-portable link: {source.relative_to(SITE)}: {href}")
            destination = (
                (source.parent / unquote(target.path)).resolve() if target.path else source
            )
            if not destination.is_relative_to(SITE):
                raise RuntimeError("Link escaped site")
            if destination.is_dir():
                destination /= "index.html"
            if not destination.is_file():
                raise RuntimeError(f"Broken link: {source.relative_to(SITE)}: {href}")
            if target.fragment and destination in pages:
                if unquote(target.fragment) not in pages[destination].ids:
                    raise RuntimeError(f"Broken anchor: {source.relative_to(SITE)}: {href}")
            links += 1
    manifest = json.loads((SITE / "build-manifest.json").read_text(encoding="utf-8"))
    release = manifest["package_version"]
    allowed_roots = {"assets", "community", "search", release}
    allowed_files = {
        "index.html",
        "404.html",
        "versions/index.html",
        "build-manifest.json",
        "sitemap.xml",
        "sitemap.xml.gz",
    }
    files = [path for path in SITE.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(SITE)
        if relative.parts[0] not in allowed_roots and relative.as_posix() not in allowed_files:
            raise RuntimeError(f"Unexpected publication file: {relative}")
        if path.suffix not in {".html", ".css", ".js", ".json", ".xml", ".gz"}:
            raise RuntimeError(f"Unexpected publication type: {relative}")
        if path.suffix == ".json" and relative.parts[0] == release:
            if relative.as_posix() not in {
                f"{release}/reference/schema-v1.json",
                f"{release}/reference/openapi-v1.json",
            }:
                raise RuntimeError("Non-schema JSON included")
    if manifest["brand_assets_included"] or manifest["public_deployment"]:
        raise RuntimeError("Unexpected publication/brand state")
    return {"html_pages": len(pages), "local_links": links, "files": len(files)}


if __name__ == "__main__":
    print(json.dumps(check()))

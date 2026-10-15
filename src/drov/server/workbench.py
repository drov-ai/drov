"""Public, fixed build assets only. Credentials and application data are never embedded."""

from importlib.resources import files
from typing import Any

POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'; object-src 'none'"
)
MEDIA = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
}


def assets() -> dict[str, tuple[bytes, str]]:
    root = files("drov.server").joinpath("static")
    result = {}
    for directory, prefix in ((root, ""), (root.joinpath("assets"), "assets/")):
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            suffix = "." + entry.name.rsplit(".", 1)[-1]
            if entry.is_file() and suffix in MEDIA:
                result["/workbench/" + prefix + entry.name] = (entry.read_bytes(), MEDIA[suffix])
    if "/workbench/index.html" in result:
        result["/workbench/"] = result.pop("/workbench/index.html")
    return result


def install(app: Any, entries: dict[str, tuple[bytes, str]]) -> None:
    from starlette.responses import RedirectResponse, Response

    async def redirect() -> Response:
        return RedirectResponse("/workbench/", status_code=307)

    app.add_api_route("/", redirect, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/workbench", redirect, methods=["GET", "HEAD"], include_in_schema=False)

    def endpoint(data: bytes, media: str) -> Any:
        async def serve() -> Response:
            return Response(data, media_type=media, headers={"Content-Security-Policy": POLICY})

        return serve

    for path, (data, media) in entries.items():
        app.add_api_route(
            path, endpoint(data, media), methods=["GET", "HEAD"], include_in_schema=False
        )

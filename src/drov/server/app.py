"""ASGI composition and security boundary. All execution remains owned by Runtime."""

import asyncio
import ipaddress
import secrets
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from drov._json import load_json
from drov.agent import Agent
from drov.artifacts import LocalArtifacts
from drov.auth import AuthAdapter, Authorizer
from drov.config import Limits
from drov.errors import ConfigurationError
from drov.runtime import Runtime
from drov.runtime.resources import ResourceBinding
from drov.server.types import ServerLimits
from drov.storage.base import Storage
from drov.storage.sqlite import SQLiteStorage
from drov.tools import PermissionPolicy
from drov.types import RunContext


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DrovApp:
    def __init__(
        self,
        *,
        agents: Mapping[str, Agent[Any]] | None = None,
        runtime: Runtime | None = None,
        storage: Storage | ResourceBinding[Storage] | None = None,
        artifacts: LocalArtifacts | ResourceBinding[LocalArtifacts] | None = None,
        policy: PermissionPolicy | None = None,
        authorizer: Authorizer | None = None,
        dependencies: Mapping[str, object] | None = None,
        limits: Limits | None = None,
        local: bool = False,
        auth: AuthAdapter | None = None,
        host: str = "127.0.0.1",
        port: int = 8000,
        allowed_hosts: Sequence[str] | None = None,
        allowed_origins: Sequence[str] | None = None,
        server_limits: ServerLimits | None = None,
    ) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigurationError("Expected a fixed server port")
        if local and (auth is not None or host != "127.0.0.1"):
            raise ConfigurationError("Local mode requires 127.0.0.1 and its startup token")
        if not local and auth is None:
            raise ConfigurationError("Choose explicit local mode or provide AuthAdapter")
        if runtime is not None:
            if any(
                value is not None
                for value in (agents, storage, artifacts, policy, authorizer, dependencies, limits)
            ):
                raise ConfigurationError("Borrowed Runtime and assembly options are exclusive")
            self.runtime = runtime
        else:
            if not agents or any(key != agent.name for key, agent in agents.items()):
                raise ConfigurationError("Agent mapping keys must match registered names")
            self.runtime = Runtime(
                agents=list(agents.values()),
                storage=storage,
                artifacts=artifacts,
                policy=policy,
                authorizer=authorizer,
                dependencies=dependencies,
                limits=limits,
            )
        if not _loopback(host) and (
            local
            or not self.runtime._explicit_authorizer
            or not allowed_origins
            or not allowed_hosts
        ):
            raise ConfigurationError(
                "Non-local binding requires AuthAdapter, Authorizer, hosts and origins"
            )
        self._owned = runtime is None
        self.local, self.auth, self.host, self.port = local, auth, host, port
        self.server_limits = ServerLimits.model_validate(
            (server_limits or ServerLimits()).model_dump()
        )
        authority = f"{host}:{port}" if ":" not in host else f"[{host}]:{port}"
        self.allowed_hosts = tuple(allowed_hosts or (authority,))
        self.allowed_origins = tuple(allowed_origins or (f"http://{authority}",))
        for value in self.allowed_hosts:
            if not value or any(c in value for c in "/\\?#@ \r\n") or "*" in value:
                raise ConfigurationError("Expected exact allowed Host authorities")
        for value in self.allowed_origins:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in ("http", "https")
                or parsed.netloc not in self.allowed_hosts
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
            ):
                raise ConfigurationError("Expected exact same-origin HTTP(S) origins")
        self.local_token: str | None = None
        self._accepting = False
        self._used = False
        self._connections = 0
        self._uploads = 0
        self._streams: set[Any] = set()
        try:
            from fastapi import FastAPI
        except ImportError as exc:
            raise ConfigurationError("Install drov[server] to construct DrovApp") from exc
        from drov.server.routes import install

        self._app: Any = FastAPI(
            title="Drov HTTP",
            version="0.1.0a0",
            lifespan=self._lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        install(self)

    @asynccontextmanager
    async def _lifespan(self, app: Any) -> AsyncIterator[None]:
        if self._used:
            raise ConfigurationError("DrovApp lifespan is single-use")
        self._used = True
        try:
            if self._owned:
                await self.runtime.__aenter__()
            else:
                self.runtime._require_open()
            if self.runtime._artifacts is not None:
                if not isinstance(self.runtime.storage, SQLiteStorage):
                    raise ConfigurationError("Uploads require SQLite")
                await self.runtime.storage._bind_uploads()
            if self.local:
                self.local_token = secrets.token_urlsafe(32)
            self._accepting = True
            yield
        finally:
            self._accepting = False
            for stream in tuple(self._streams):
                await stream.aclose()
            if self._owned:
                await self.runtime.aclose()
            self.local_token = None

    async def _identity(self, headers: dict[str, str]) -> RunContext:
        from drov.server.routes import HTTPFailure

        credential = headers.get("authorization", "")
        scheme, _, token = credential.partition(" ")
        if (
            scheme.lower() != "bearer"
            or not token
            or len(token) > 8192
            or any(c.isspace() for c in token)
        ):
            raise HTTPFailure(401, "unauthenticated")
        if self.local:
            if self.local_token is None or not secrets.compare_digest(token, self.local_token):
                raise HTTPFailure(401, "unauthenticated")
            return RunContext(tenant_id="local", user_id="local")
        assert self.auth is not None
        try:
            async with asyncio.timeout(self.runtime.limits.verify_timeout_seconds):
                context = await self.auth.authenticate(token)
            return RunContext.from_json(context.model_dump_json())
        except Exception:
            raise HTTPFailure(401, "unauthenticated") from None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        from starlette.requests import ClientDisconnect

        from drov.server.routes import HTTPFailure, error_response

        request_id = uuid4().hex
        started = False

        async def bounded_send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            async with asyncio.timeout(self.server_limits.send_seconds):
                await send(message)

        admitted = False
        try:
            raw_headers = scope.get("headers", [])
            headers: dict[str, str] = {}
            for name, value in raw_headers:
                key = name.decode("latin-1").lower()
                if key in headers and key in (
                    "host",
                    "origin",
                    "authorization",
                    "content-length",
                    "content-type",
                    "last-event-id",
                ):
                    raise HTTPFailure(400, "invalid_headers")
                headers[key] = value.decode("latin-1")
            if headers.get("host") not in self.allowed_hosts:
                raise HTTPFailure(400, "invalid_host")
            if "origin" in headers and headers["origin"] not in self.allowed_origins:
                raise HTTPFailure(403, "invalid_origin")
            if self.local:
                peer = scope.get("client")
                server = scope.get("server")
                if (peer and not _loopback(peer[0])) or (server and not _loopback(server[0])):
                    raise HTTPFailure(403, "local_only")
            if not self._accepting:
                raise HTTPFailure(503, "service_unavailable")
            from urllib.parse import parse_qs

            if {"token", "access_token", "api_key", "authorization"} & parse_qs(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            ).keys():
                raise HTTPFailure(400, "credential_in_url")
            context = await self._identity(headers)
            if self._connections >= self.server_limits.max_connections:
                raise HTTPFailure(429, "capacity_exceeded")
            self._connections += 1
            admitted = True
            scope["drov.context"], scope["drov.request_id"] = context, request_id
            scope["drov.headers"] = headers
            if not (scope["path"] == "/v1/artifacts" and scope["method"] == "POST"):
                body = bytearray()
                async with asyncio.timeout(self.server_limits.upload_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > self.server_limits.max_json_bytes:
                            raise HTTPFailure(413, "body_too_large")
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
                if body and scope["method"] in ("GET", "HEAD", "OPTIONS"):
                    raise HTTPFailure(400, "unexpected_body")
                if (
                    body
                    and headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    raise HTTPFailure(415, "unsupported_media_type")
                if body:
                    try:
                        load_json(bytes(body))
                    except (ValueError, RecursionError):
                        raise HTTPFailure(422, "invalid_request") from None
                received = False

                async def replay() -> Any:
                    nonlocal received
                    if not received:
                        received = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self._app(scope, replay, bounded_send)
            else:
                await self._app(scope, receive, bounded_send)
        except HTTPFailure as exc:
            if not started:
                await error_response(exc.status, exc.code, request_id)(scope, receive, bounded_send)
        except (TimeoutError, OSError, ClientDisconnect):
            if not started:
                await error_response(503, "transport_unavailable", request_id)(
                    scope, receive, bounded_send
                )
        except Exception:
            # FastAPI rethrows errors after its safe 500 handler; never expose exception text.
            if not started:
                await error_response(500, "internal_error", request_id)(
                    scope, receive, bounded_send
                )
        finally:
            subscription = scope.get("drov.subscription")
            if subscription is not None:
                self._streams.discard(subscription)
                await subscription.aclose()
            if admitted:
                self._connections -= 1

    def openapi(self) -> dict[str, Any]:
        document = cast(dict[str, Any], self._app.openapi())
        document.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {"type": "http", "scheme": "bearer"}
        }
        document["security"] = [{"BearerAuth": []}]
        return document

    async def serve(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self,
            host=self.host,
            port=self.port,
            workers=1,
            lifespan="on",
            proxy_headers=False,
            access_log=False,
            timeout_graceful_shutdown=5,
        )
        await uvicorn.Server(config).serve()

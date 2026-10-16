"""Optional FastAPI routes: validate transport, delegate, serialize authorized values."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Annotated, Any, TypeVar, cast
from urllib.parse import quote

from fastapi import Header, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response, StreamingResponse

from drov.artifacts import ArtifactInfo, ArtifactRef
from drov.artifacts.uploads import UploadReceipt, Uploads, UploadTarget
from drov.auth import Resource
from drov.errors import (
    ArtifactUnavailable,
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    ContentGone,
    CursorExpired,
    DrovError,
    InvalidCursor,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.server.types import (
    AgentCatalog,
    AgentSummary,
    EmptyBody,
    ErrorEnvelope,
    ResponseBody,
    StartBody,
    VerifyBody,
)
from drov.types import (
    CancelReceipt,
    ContextManifest,
    DurableEvent,
    ReconciliationDecision,
    ResponseReceipt,
    RunContext,
    RunInspection,
    RunRef,
    RunSnapshot,
    SessionPage,
    SessionView,
    UnknownDurableEvent,
)

if TYPE_CHECKING:
    from drov.server import DrovApp


class HTTPFailure(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code


def error_response(status: int, code: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": "Request could not be completed",
                "request_id": request_id,
            }
        },
        status_code=status,
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def failure(exc: Exception) -> tuple[int, str]:
    for cls, status in (
        (RecordNotFound, 404),
        (PermissionDenied, 403),
        (ArtifactUnavailable, 410),
        (ContentGone, 410),
        (CursorExpired, 410),
        (Conflict, 409),
        (CapacityExceeded, 429),
        (PersistenceUnavailable, 503),
        (InvalidCursor, 400),
        (ConfigurationError, 400),
    ):
        if isinstance(exc, cls):
            return status, cast(DrovError, exc).code
    return 503, "service_unavailable"


def actor(request: Request) -> RunContext:
    return cast(RunContext, request.scope["drov.context"])


F = TypeVar("F", bound=Callable[..., Any])


def _route(decorator: Any) -> Callable[[F], F]:
    """Preserve handler signatures even when the optional Web stubs are absent."""
    return cast(Callable[[F], F], decorator)


def install(service: "DrovApp") -> None:
    app, runtime = service._app, service.runtime
    # Explicit text avoids Python HTTPStatus phrase changes altering the generated contract.
    descriptions = {
        400: "Invalid request",
        401: "Authentication required",
        403: "Permission denied",
        404: "Resource unavailable",
        409: "Conflict",
        410: "Content unavailable",
        411: "Content length required",
        413: "Request body too large",
        415: "Unsupported media type",
        422: "Invalid request fields",
        429: "Capacity exhausted",
        500: "Internal error",
        503: "Service unavailable",
    }
    errors: dict[int | str, dict[str, Any]] = {
        status: {"model": ErrorEnvelope, "description": description}
        for status, description in descriptions.items()
    }

    @_route(app.exception_handler(HTTPFailure))
    async def http_failure(request: Request, exc: HTTPFailure) -> JSONResponse:
        return error_response(exc.status, exc.code, request.scope["drov.request_id"])

    @_route(app.exception_handler(DrovError))
    async def drov_error(request: Request, exc: DrovError) -> JSONResponse:
        status, code = failure(exc)
        return error_response(status, code, request.scope["drov.request_id"])

    @_route(app.exception_handler(ValueError))
    async def bad_value(request: Request, exc: ValueError) -> JSONResponse:
        return error_response(422, "invalid_request", request.scope["drov.request_id"])

    @_route(app.exception_handler(RequestValidationError))
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "invalid_request", request.scope["drov.request_id"])

    @_route(app.exception_handler(HTTPException))
    async def unknown_route(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(exc.status_code, "http_error", request.scope["drov.request_id"])

    @_route(app.exception_handler(Exception))
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", request.scope["drov.request_id"])

    @_route(app.get("/openapi.json", include_in_schema=False))
    async def openapi() -> dict[str, Any]:
        return service.openapi()

    @_route(app.get("/v1/agents", response_model=AgentCatalog, responses=errors))
    async def agents(request: Request) -> AgentCatalog:
        await runtime._runs.authorize(actor(request), Resource(owner=actor(request)), ("read",))
        return AgentCatalog(
            items=[AgentSummary(name=name, revision=revision) for name, revision in runtime._agents]
        )

    @_route(app.post("/v1/runs", status_code=202, response_model=RunRef, responses=errors))
    async def start(body: StartBody, request: Request) -> RunRef:
        key = (body.agent_name, body.revision)
        if key not in runtime._agents:
            raise RecordNotFound("Agent is unavailable")
        refs = []
        for artifact_id in body.artifact_ids:
            info = await runtime.get_artifact(
                artifact_id, context=actor(request), include_content=True
            )
            if info.state != "ready":
                raise ArtifactUnavailable("Artifact is unavailable")
            refs.append(
                ArtifactRef(
                    artifact_id=info.artifact_id,
                    sha256=info.sha256,
                    size_bytes=info.size_bytes,
                    media_type=info.media_type,
                )
            )
        if body.upload_ids and runtime._artifacts is None:
            raise ConfigurationError("Files are not configured")
        return await runtime._runs.start(
            runtime._agents[key],
            body.input,
            context=actor(request),
            session_id=body.session_id,
            request_key=body.request_key,
            limits=body.limits,
            artifacts=refs,
            _upload_ids=tuple(body.upload_ids),
        )

    @_route(app.get("/v1/runs/{run_id}", response_model=RunInspection, responses=errors))
    async def inspect(
        run_id: str,
        request: Request,
        after: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        include_content: bool = False,
    ) -> RunInspection:
        return await runtime.inspect(
            run_id,
            context=actor(request),
            after=after,
            limit=limit,
            include_content=include_content,
        )

    @_route(
        app.post(
            "/v1/runs/{run_id}/responses",
            status_code=202,
            response_model=ResponseReceipt,
            responses=errors,
        )
    )
    async def respond(run_id: str, body: ResponseBody, request: Request) -> ResponseReceipt:
        return await runtime.respond(
            run_id, context=actor(request), request_id=body.request_id, response=body.response
        )

    @_route(
        app.post(
            "/v1/runs/{run_id}/verify", response_model=ReconciliationDecision, responses=errors
        )
    )
    async def verify(run_id: str, body: VerifyBody, request: Request) -> ReconciliationDecision:
        return await runtime.verify(run_id, context=actor(request), request_id=body.request_id)

    @_route(
        app.post(
            "/v1/runs/{run_id}/cancel",
            status_code=202,
            response_model=CancelReceipt,
            responses=errors,
        )
    )
    async def cancel(run_id: str, body: EmptyBody, request: Request) -> CancelReceipt:
        return await runtime.cancel(run_id, context=actor(request))

    @_route(
        app.post(
            "/v1/runs/{run_id}/recover",
            status_code=202,
            response_model=RunSnapshot,
            responses=errors,
        )
    )
    async def recover(run_id: str, body: EmptyBody, request: Request) -> RunSnapshot:
        return cast(RunSnapshot, await runtime.recover(run_id, context=actor(request), wait=False))

    @_route(app.get("/v1/sessions", response_model=SessionPage, responses=errors))
    async def sessions(
        request: Request, after: str | None = None, limit: int = Query(100, ge=1, le=500)
    ) -> SessionPage:
        return await runtime.sessions(context=actor(request), after=after, limit=limit)

    @_route(app.get("/v1/session", response_model=SessionView, responses=errors))
    @_route(app.get("/v1/sessions/{session_id}", response_model=SessionView, responses=errors))
    async def session(
        session_id: str,
        request: Request,
        after: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> SessionView:
        return await runtime.session(session_id, context=actor(request), after=after, limit=limit)

    @_route(
        app.get(
            "/v1/runs/{run_id}/contexts/{model_call_id}",
            response_model=ContextManifest,
            responses=errors,
        )
    )
    async def context(
        run_id: str, model_call_id: str, request: Request, include_content: bool = False
    ) -> ContextManifest:
        return await runtime.get_context(
            run_id, model_call_id, context=actor(request), include_content=include_content
        )

    @_route(
        app.get(
            "/v1/runs/{run_id}/events",
            response_class=StreamingResponse,
            responses={
                **errors,
                200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
            },
        )
    )
    async def events(
        run_id: str,
        request: Request,
        after: str | None = None,
        last_event_id: str | None = Header(None),
        include_content: bool = False,
    ) -> Response:
        if after is not None and last_event_id is not None and after != last_event_id:
            raise InvalidCursor("Conflicting event cursors")
        selected = after if after is not None else last_event_id
        # Ahead/invalid cursors and permissions fail before HTTP headers are committed.
        await runtime.inspect(
            run_id, context=actor(request), after=selected, limit=1, include_content=include_content
        )
        subscription = runtime.events(
            run_id, context=actor(request), after=selected, include_content=include_content
        )

        async def frames() -> AsyncIterator[bytes]:
            try:
                async for event in subscription:
                    current = await service._identity(request.scope["drov.headers"])
                    if current != actor(request):
                        raise HTTPFailure(401, "unauthenticated")
                    # Authentication itself may await while a prefetched page loses access.
                    await runtime._runs.visible(run_id, current, include_content)
                    prefix = (
                        f"id: {event.run_id}:{event.seq}\n"
                        if isinstance(event, (DurableEvent, UnknownDurableEvent))
                        else ""
                    )
                    yield (
                        prefix + f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
                    ).encode()
            except (DrovError, HTTPFailure, ValueError) as exc:
                code = (
                    exc.code if isinstance(exc, (DrovError, HTTPFailure)) else "stream_unavailable"
                )
                import json

                yield (
                    "event: stream.error\ndata: "
                    + json.dumps(
                        {
                            "durable": False,
                            "type": "stream.error",
                            "run_id": run_id,
                            "data": {"code": code, "message": "Subscription ended"},
                        }
                    )
                    + "\n\n"
                ).encode()

        await subscription.__aenter__()
        service._streams.add(subscription)
        request.scope["drov.subscription"] = subscription
        return StreamingResponse(
            frames(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @_route(
        app.post(
            "/v1/artifacts",
            status_code=201,
            response_model=UploadReceipt,
            responses=errors,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                }
            },
        )
    )
    async def upload(request: Request, target: Annotated[UploadTarget, Query()]) -> UploadReceipt:
        if service._uploads >= service.server_limits.upload_concurrency:
            raise CapacityExceeded("Upload capacity exhausted")
        store = runtime._files.store()
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdigit():
            raise HTTPFailure(411, "length_required")
        size = int(length)
        if size > store.limits.max_input_bytes:
            raise HTTPFailure(413, "body_too_large")
        if request.headers.get("content-type", "").lower() != "application/octet-stream":
            raise HTTPFailure(415, "unsupported_media_type")
        service._uploads += 1
        try:
            uploads = Uploads(runtime)
            reservation = await runtime._runs.control(
                lambda: uploads.reserve(actor(request), target, size)
            )
            data = bytearray()
            async with asyncio.timeout(service.server_limits.upload_seconds):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > size:
                        raise HTTPFailure(413, "body_too_large")
                    data.extend(chunk)
            return await runtime._runs.control(
                lambda: uploads.stage(actor(request), reservation, bytes(data))
            )
        finally:
            service._uploads -= 1

    @_route(app.get("/v1/artifacts/{artifact_id}", response_model=ArtifactInfo, responses=errors))
    async def artifact(
        artifact_id: str, request: Request, include_content: bool = False
    ) -> ArtifactInfo:
        return await runtime.get_artifact(
            artifact_id, context=actor(request), include_content=include_content
        )

    @_route(
        app.get(
            "/v1/artifacts/{artifact_id}/content",
            response_class=StreamingResponse,
            responses={
                **errors,
                200: {
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    }
                },
            },
        )
    )
    async def content(artifact_id: str, request: Request) -> Response:
        info = await runtime.get_artifact(artifact_id, context=actor(request), include_content=True)
        if info.state != "ready":
            raise ArtifactUnavailable("Artifact is unavailable")
        try:
            data = await runtime.read_artifact(artifact_id, context=actor(request))
        except PersistenceUnavailable:
            current = await runtime.get_artifact(artifact_id, context=actor(request))
            if current.state == "unavailable":
                raise ArtifactUnavailable("Artifact bytes are unavailable") from None
            raise

        async def chunks() -> AsyncIterator[bytes]:
            for offset in range(0, len(data), 65536):
                current = await service._identity(request.scope["drov.headers"])
                if current != actor(request):
                    return
                await runtime.get_artifact(artifact_id, context=current, include_content=True)
                yield data[offset : offset + 65536]

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    "attachment; filename=\"artifact\"; filename*=UTF-8''"
                    + quote(info.name or "artifact", safe="")
                ),
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Length": str(len(data)),
            },
        )

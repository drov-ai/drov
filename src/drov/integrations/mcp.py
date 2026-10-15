"""Optional, tools-only MCP stdio bridge with frozen discovery and explicit ownership."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import JsonValue, TypeAdapter

from drov._json import canonical_json, load_json
from drov.errors import ConfigurationError, ShutdownIncomplete, UnsupportedSchema
from drov.integrations._mcp_tool import MCPConnection, MCPTool
from drov.tools.schema import JsonCodec
from drov.types import Effect, ScopedIdentifier

__all__ = ["MCPStdio", "MCPTool", "MCPToolPolicy"]


@dataclass(frozen=True)
class MCPToolPolicy:
    effect: Effect = "unknown"
    approval: Literal["required", "policy"] = "policy"

    def __post_init__(self) -> None:
        if self.effect not in ("read", "write", "unknown") or self.approval not in (
            "required",
            "policy",
        ):
            raise ConfigurationError("Invalid application MCP tool policy")


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validator(schema: dict[str, Any]) -> Any:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        raise ConfigurationError("MCP requires the drov[mcp] extra") from None
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise UnsupportedSchema("Invalid MCP JSON schema") from None
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class _SchemaCodec(JsonCodec):
    def decode(self, value: JsonValue) -> JsonValue:
        value = load_json(canonical_json(value))
        if not _validator(self.schema).is_valid(value):
            raise ValueError("MCP value does not match the frozen schema")
        return value

    def encode(self, value: Any) -> JsonValue:
        return self.decode(value)


_RESULT_JSON = canonical_json(
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"type": {"enum": ["text"]}, "text": {"type": "string"}},
                    "required": ["type", "text"],
                    "additionalProperties": False,
                },
            },
            "structuredContent": {"type": ["object", "null"]},
        },
        "required": ["content", "structuredContent"],
        "additionalProperties": False,
    }
)


def _schema(raw: object, *, root: bool = False, depth: int = 0) -> dict[str, Any]:
    # A deliberately bounded subset shared with Drov's model-facing tools. A missing
    # additionalProperties is narrowed to false; dynamic/open dictionaries are rejected.
    if depth > 16 or type(raw) is not dict:
        raise UnsupportedSchema("MCP schema is too deep or outside the supported subset")
    _validator(raw)
    accepted = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
        "description",
        "title",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    }
    if set(raw) - accepted:
        raise UnsupportedSchema(
            "Unsupported MCP schema keyword; provide a compatible server schema"
        )
    schema = {key: value for key, value in raw.items() if key != "title"}
    if "anyOf" in schema:
        choices = schema["anyOf"]
        if type(choices) is not list or len(choices) != 2 or {"type": "null"} not in choices:
            raise UnsupportedSchema("MCP schemas support only nullable unions")
        schema["anyOf"] = [_schema(item, depth=depth + 1) for item in choices]
    elif schema.get("type") == "object":
        if schema.get("additionalProperties", False) is not False:
            raise UnsupportedSchema("Open MCP argument objects are unsupported")
        properties = schema.get("properties", {})
        if type(properties) is not dict or any(type(key) is not str for key in properties):
            raise UnsupportedSchema("Invalid MCP object properties")
        schema["properties"] = {
            key: _schema(value, depth=depth + 1) for key, value in properties.items()
        }
        schema["required"] = sorted(schema.get("required", []))
        schema["additionalProperties"] = False
    elif schema.get("type") == "array":
        schema["items"] = _schema(schema.get("items"), depth=depth + 1)
    elif schema.get("type") not in ("string", "integer", "number", "boolean", "null"):
        raise UnsupportedSchema("MCP schema requires an explicit supported type")
    if root and schema.get("type") != "object":
        raise UnsupportedSchema("MCP tool arguments must be an object")
    validator = _validator(schema)
    if "default" in schema and not validator.is_valid(schema["default"]):
        raise UnsupportedSchema("Invalid MCP schema default")
    return schema


def _make_tool(
    snapshot: str,
    *,
    dependency: str,
    source_hash: str,
    revision: str,
    name: str,
    effect: Effect,
    approval: Literal["required", "policy"],
) -> MCPTool:
    raw = cast(dict[str, Any], load_json(snapshot))
    if (raw.get("execution") or {}).get("taskSupport") == "required":
        raise ConfigurationError("MCP task-required tools are unsupported")
    parameters = _schema(raw["inputSchema"], root=True)
    # Missing optional fields remain absent: JSON Schema defaults are annotations,
    # not permission to change the arguments approved by the application.
    codecs = {key: _SchemaCodec(object, value) for key, value in parameters["properties"].items()}

    context_name = "_drov_context"
    while context_name in codecs:
        context_name += "_"

    async def invoke(**arguments: Any) -> Any:
        context = arguments.pop(context_name)
        connection = context.deps.get(dependency)
        if not isinstance(connection, MCPConnection):
            raise ConfigurationError("MCP dependency is not configured")
        return await connection.call(definition, arguments)

    definition = MCPTool(
        name=name,
        revision=revision,
        description=raw.get("description") or "",
        effect=effect,
        approval=approval,
        parameters=parameters,
        result_codec=_SchemaCodec(object, cast(dict[str, Any], load_json(_RESULT_JSON))),
        argument_codecs=MappingProxyType(codecs),
        defaults=MappingProxyType({}),
        context_parameter=context_name,
        function=invoke,
        dependency=dependency,
        remote_name=raw["name"],
        source_hash=source_hash,
        snapshot=snapshot,
    )
    return definition


class _Session:
    """One task owns the SDK's nested AnyIO scopes from enter through exit."""

    def __init__(self, server: MCPStdio) -> None:
        self.server = server
        self.client: Any = None
        self.transport_closed = False
        self.protocol: str | None = None
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.ready.add_done_callback(
            lambda future: future.exception() if not future.cancelled() else None
        )
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self._own(), name="drov-mcp-owner")
        self.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def _own(self) -> None:
        entered = False
        try:
            from mcp import Client
            from mcp.client.stdio import (
                DEFAULT_INHERITED_ENV_VARS,
                StdioServerParameters,
                stdio_client,
            )

            server = self.server
            # Override SDK safe defaults too: the child receives only the host's explicit
            # whitelist. Do not mutate the host environment or inherit model credentials.
            environment = dict.fromkeys(DEFAULT_INHERITED_ENV_VARS, "")
            environment.update(server._environment)
            parameters = StdioServerParameters(
                command=server._command, args=list(server._args), env=environment, cwd=server._cwd
            )
            # Server stderr may contain secrets; it is never forwarded to logs or events.
            with open(os.devnull, "w") as errlog:  # noqa: ASYNC230 - null sink, no user file I/O
                async with stdio_client(parameters, errlog=errlog) as streams:
                    entered = True
                    try:
                        async with Client(
                            streams_transport(streams),
                            mode="legacy",
                            cache=None,
                            read_timeout_seconds=server._timeout,
                        ) as client:
                            self.client = client
                            self.protocol = client.protocol_version
                            if client.server_capabilities.tools is None:
                                raise ConfigurationError("MCP server does not advertise tools")
                            if not self.ready.done():
                                self.ready.set_result(None)
                            await self.stop.wait()
                    except BaseException:
                        if not self.ready.done():
                            self.ready.set_exception(
                                ConfigurationError("MCP stdio initialization failed")
                            )
                        # The transport still exits normally and owns process cleanup.
                self.transport_closed = True
            self.client = None
        except BaseException as exc:
            if not entered and isinstance(exc, (ImportError, OSError)):
                self.transport_closed = True
            if not self.ready.done():
                self.ready.set_exception(ConfigurationError("MCP stdio initialization failed"))
            raise

    async def started(self) -> None:
        try:
            async with asyncio.timeout(self.server._timeout):
                await asyncio.shield(self.ready)
        except BaseException:
            self.stop.set()
            # Cancel initialization through its owner only. After readiness the owner is
            # never cancelled, so cleanup cannot be interrupted by a waiting caller.
            if not self.ready.done():
                self.task.cancel()
            await self.close()
            raise

    async def close(self) -> None:
        self.stop.set()
        done, _ = await asyncio.wait({self.task}, timeout=self.server._close_timeout)
        if not done:
            raise ShutdownIncomplete("MCP process cleanup is still active")
        if not self.transport_closed:
            raise ShutdownIncomplete("MCP transport cleanup could not be confirmed") from None


def streams_transport(streams: Any) -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def transport() -> Any:
        yield streams

    return transport()


class MCPStdio(MCPConnection):
    """Host-configured stdio server. Constructing it performs no I/O.

    ``await discover()`` opens and closes a preflight process and freezes its listing.
    Bind this object to Runtime using ``ResourceBinding.owned(server)``; Runtime opens
    a fresh process and checks the snapshot. Borrowed bindings must already be open.
    Environment values are excluded from fingerprints; changing them requires revision.
    """

    def __init__(
        self,
        *,
        namespace: str,
        revision: str,
        command: str,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        inherit_env: Sequence[str] = (),
        timeout_seconds: float = 10,
        close_timeout_seconds: float = 10,
        max_pages: int = 32,
        max_tools: int = 256,
        max_listing_bytes: int = 262144,
    ) -> None:
        for identifier in (namespace, revision):
            TypeAdapter(ScopedIdentifier).validate_python(identifier, strict=True)
        if not Path(command).is_absolute() or "\0" in command:
            raise ConfigurationError("MCP command must be a host-configured absolute executable")
        if isinstance(args, (str, bytes)) or any(type(v) is not str or "\0" in v for v in args):
            raise ConfigurationError("Invalid MCP arguments")
        if cwd is not None and (not Path(cwd).is_absolute() or "\0" in str(cwd)):
            raise ConfigurationError("MCP cwd must be absolute")
        for value in (timeout_seconds, close_timeout_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ConfigurationError("MCP timeouts must be positive finite seconds")
        for value in (max_pages, max_tools, max_listing_bytes):
            if type(value) is not int or value < 1:
                raise ConfigurationError("MCP discovery limits must be positive integers")
        if isinstance(inherit_env, (str, bytes)):
            raise ConfigurationError("MCP inherit_env must contain explicit variable names")
        environment = {key: os.environ[key] for key in inherit_env if key in os.environ}
        environment.update(env or {})
        if any(
            type(key) is not str
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or type(value) is not str
            or "\0" in value
            for key, value in environment.items()
        ):
            raise ConfigurationError("Invalid MCP environment whitelist")
        self.namespace, self.revision = namespace, revision
        self.dependency = f"mcp.{namespace}"
        TypeAdapter(ScopedIdentifier).validate_python(self.dependency, strict=True)
        self._command, self._args = command, tuple(args)
        self._cwd = str(cwd) if cwd is not None else None
        self._environment = MappingProxyType(environment)
        self._timeout, self._close_timeout = timeout_seconds, close_timeout_seconds
        self._max_pages, self._max_tools = max_pages, max_tools
        self._max_listing_bytes = max_listing_bytes
        self._source_hash = _digest(
            {
                "namespace": namespace,
                "revision": revision,
                "command": command,
                "args": list(args),
                "cwd": self._cwd,
                "env_names": sorted(environment),
                "timeout": timeout_seconds,
            }
        )
        self._protocol: str | None = None
        self._manifest: str | None = None
        self._tools: tuple[MCPTool, ...] = ()
        self._session: _Session | None = None
        self._preflight: _Session | None = None
        self._used = False
        self._opened = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._gate = asyncio.Lock()
        self._calls: set[asyncio.Task[Any]] = set()
        self._closing: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return (
            self._opened
            and self._session is not None
            and not self._session.task.done()
            and self._closing is None
        )

    @property
    def protocol_version(self) -> str | None:
        return self._protocol

    @property
    def tools(self) -> tuple[MCPTool, ...]:
        return self._tools

    def _require_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ConfigurationError("MCP connection belongs to another event loop")
        self._loop = loop

    async def _listing(self, session: _Session) -> str:
        from mcp.types import PaginatedRequestParams

        tools: dict[str, dict[str, Any]] = {}
        cursors: set[str] = set()
        cursor: str | None = None
        async with asyncio.timeout(self._timeout):
            for _ in range(self._max_pages):
                page = await session.client.session.list_tools(
                    params=PaginatedRequestParams(cursor=cursor)
                )
                for tool in page.tools:
                    raw = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if tool.name in tools:
                        raise ConfigurationError("MCP discovery returned duplicate names")
                    tools[tool.name] = raw
                if len(tools) > self._max_tools:
                    raise ConfigurationError("MCP discovery exceeded tool limit")
                manifest = canonical_json([tools[name] for name in sorted(tools)])
                if len(manifest.encode("utf-8")) > self._max_listing_bytes:
                    raise ConfigurationError("MCP discovery exceeded listing limit")
                cursor = page.next_cursor
                if cursor is None:
                    return manifest
                if cursor in cursors:
                    raise ConfigurationError("MCP discovery repeated a cursor")
                cursors.add(cursor)
        raise ConfigurationError("MCP discovery exceeded page limit")

    async def discover(
        self,
        *,
        policies: Mapping[str, MCPToolPolicy] | None = None,
    ) -> tuple[MCPTool, ...]:
        try:
            importlib.import_module("mcp")
        except ImportError:
            raise ConfigurationError("MCP requires the drov[mcp] extra") from None
        self._require_loop()
        async with self._gate:
            if self._used or self._manifest is not None or self._closing is not None:
                raise ConfigurationError(
                    "Discovery is single-use; create a new definition for changes"
                )
            if any(not isinstance(policy, MCPToolPolicy) for policy in (policies or {}).values()):
                raise ConfigurationError("MCP policies must be explicit MCPToolPolicy values")
            session = self._preflight = _Session(self)
            try:
                await session.started()
                manifest = await self._listing(session)
                source_hash = _digest(
                    {
                        "config": self._source_hash,
                        "protocol": session.protocol,
                        "manifest": manifest,
                    }
                )
                discovered: list[MCPTool] = []
                raw_tools = cast(list[dict[str, Any]], load_json(manifest))
                if set(policies or {}) - {raw["name"] for raw in raw_tools}:
                    raise ConfigurationError("MCP policy names were not discovered")
                for raw in raw_tools:
                    remote = raw["name"]
                    # Hash encoding is stable and injective for practical purposes, even
                    # for MCP names not valid as Drov identifiers; no lossy punctuation map.
                    name = f"mcp.{self.namespace}.t_{_digest(remote)[:32]}"
                    TypeAdapter(ScopedIdentifier).validate_python(name, strict=True)
                    policy = (policies or {}).get(remote, MCPToolPolicy())
                    discovered.append(
                        _make_tool(
                            canonical_json(raw),
                            dependency=self.dependency,
                            source_hash=source_hash,
                            revision=self.revision,
                            name=name,
                            effect=policy.effect,
                            approval=policy.approval,
                        )
                    )
            finally:
                await asyncio.shield(session.close())
            if len({tool.name for tool in discovered}) != len(discovered):
                raise ConfigurationError("MCP logical tool name collision")
            self._preflight = None
            self._source_hash = source_hash
            self._protocol = session.protocol
            self._manifest, self._tools = manifest, tuple(discovered)
            return self._tools

    async def open(self) -> None:
        self._require_loop()
        async with self._gate:
            if self._used or self._manifest is None or self._closing is not None:
                raise ConfigurationError("MCP open requires prior discovery and a fresh connection")
            self._used = True
            session = self._session = _Session(self)
            try:
                await session.started()
                if (
                    session.protocol != self._protocol
                    or await self._listing(session) != self._manifest
                ):
                    raise ConfigurationError(
                        "MCP listing changed; rediscover and register a new revision"
                    )
                self._opened = True
            except BaseException:
                await session.close()
                raise

    def validate_tool(self, tool: MCPTool) -> None:
        self._require_loop()
        if not self.is_open or tool.source_hash != self._source_hash or tool not in self._tools:
            raise ConfigurationError("MCP tool is not bound to its discovered connection")

    async def call(self, tool: MCPTool, arguments: dict[str, Any]) -> JsonValue:
        self.validate_tool(tool)
        assert self._session is not None
        session = self._session

        # This method is private to compiled tools via injected ToolContext in normal use.
        async def invoke() -> JsonValue:
            if session.protocol != self._protocol or await self._listing(session) != self._manifest:
                raise ConfigurationError("MCP listing changed before dispatch")
            async with asyncio.timeout(self._timeout):
                # Raw session API performs exactly one tools/call. No header-mismatch
                # re-list/resend, input-required loop, sampling, or elicitation callbacks.
                result = await session.client.session.call_tool(tool.remote_name, arguments)
            if result.is_error:
                raise ValueError("MCP tool reported an error")
            content: list[dict[str, str]] = []
            for item in result.content:
                if item.type != "text":
                    raise ValueError("Only MCP text and structured JSON results are supported")
                content.append({"type": "text", "text": item.text})
            return tool.result_codec.encode(
                {
                    "content": content,
                    "structuredContent": result.structured_content,
                }
            )

        task = asyncio.create_task(invoke(), name="drov-mcp-call")
        self._calls.add(task)
        try:
            return await asyncio.shield(task)
        except BaseException:
            # Abort the complete connection on timeout/cancellation/transport failure.
            # The wrapper remains alive until the real process cleanup has finished.
            task.cancel()
            await asyncio.shield(self.aclose())
            raise
        finally:
            self._calls.discard(task)

    async def _close(self) -> None:
        for task in tuple(self._calls):
            task.cancel()
        if self._preflight is not None:
            await self._preflight.close()
        if self._session is not None:
            await self._session.close()
        if self._calls:
            await asyncio.gather(*self._calls, return_exceptions=True)

    async def aclose(self) -> None:
        self._require_loop()
        if self._closing is None or self._closing.done() and self._closing.exception() is not None:
            self._closing = asyncio.create_task(self._close(), name="drov-mcp-close")
        await asyncio.shield(self._closing)

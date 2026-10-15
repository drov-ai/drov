"""Bounded owner-scope session views, signed query-bound keyset cursors."""

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from drov.auth import Resource
from drov.errors import InvalidCursor, RecordNotFound
from drov.storage.records import SessionScope
from drov.storage.transaction import Transaction
from drov.types import RunContext, RunRef, SessionPage, SessionSummary, SessionView

if TYPE_CHECKING:
    from drov.runtime import Runtime


def cursor(tx: Transaction, scope: RunContext, query: str, key: tuple[datetime, str]) -> str:
    payload = json.dumps(
        [scope.tenant_id, scope.user_id, query, key[0].isoformat(), key[1]], separators=(",", ":")
    ).encode()
    signed = hmac.new(tx._secret, b"drov.page.v1:" + payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signed).decode().rstrip("=")


def before(
    tx: Transaction, scope: RunContext, query: str, value: str | None
) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        if len(value) > 4096:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        body, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(
            signature, hmac.new(tx._secret, b"drov.page.v1:" + body, hashlib.sha256).digest()
        ):
            raise ValueError
        data = json.loads(body)
        if data[:3] != [scope.tenant_id, scope.user_id, query] or len(data) != 5:
            raise ValueError
        return datetime.fromisoformat(data[3]), data[4]
    except (ValueError, TypeError, IndexError, UnicodeError) as exc:
        raise InvalidCursor("Invalid session cursor") from exc


async def sessions(
    runtime: "Runtime", context: RunContext, after: str | None, limit: int
) -> SessionPage:
    runtime._require_open()
    Transaction._page_limit(limit)
    if limit > runtime.limits.max_page_size:
        raise ValueError("Invalid page size")
    async with runtime.storage.transaction() as tx:
        rows = await tx.sessions(
            context, limit=min(500, limit + 1), before=before(tx, context, "sessions", after)
        )
        more = len(rows) > limit
        selected = rows[:limit]
        if limit == 500 and selected:
            more = bool(
                await tx.sessions(
                    context,
                    limit=1,
                    before=(selected[-1].created_at, selected[-1].scope.session_id),
                )
            )
        next_cursor = (
            cursor(
                tx, context, "sessions", (selected[-1].created_at, selected[-1].scope.session_id)
            )
            if selected
            else None
        )
    items = []
    for row in selected:
        try:
            await runtime._runs.authorize(
                context, Resource(owner=context, session_id=row.scope.session_id), ("read",)
            )
        except RecordNotFound:
            continue
        items.append(
            SessionSummary(
                session_id=row.scope.session_id,
                agent_name=row.agent_name,
                active_run_id=row.active_run_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return SessionPage(items=items, next_cursor=next_cursor, has_more=more)


async def session(
    runtime: "Runtime", session_id: str, context: RunContext, after: str | None, limit: int
) -> SessionView:
    runtime._require_open()
    Transaction._page_limit(limit)
    if limit > runtime.limits.max_page_size:
        raise ValueError("Invalid page size")
    scope = SessionScope(**context.model_dump(), session_id=session_id)
    resource = Resource(owner=context, session_id=session_id)
    await runtime._runs.authorize(context, resource, ("read",))
    query = "runs:" + session_id
    async with runtime.storage.transaction() as tx:
        summary = await tx.get_session(scope)
        rows = await tx.runs(
            scope, limit=min(500, limit + 1), before=before(tx, context, query, after)
        )
        selected = rows[:limit]
        more = len(rows) > limit
        if limit == 500 and selected:
            more = bool(
                await tx.runs(
                    scope,
                    limit=1,
                    before=(selected[-1].created_at, selected[-1].state.run_id),
                )
            )
        next_cursor = (
            cursor(tx, context, query, (selected[-1].created_at, selected[-1].state.run_id))
            if selected
            else None
        )
    refs = []
    for row in selected:
        try:
            await runtime._runs.authorize(context, runtime._runs.resource(row), ("read",))
        except RecordNotFound:
            continue
        refs.append(RunRef(run_id=row.state.run_id, state_version=row.state.state_version))
    await runtime._runs.authorize(context, resource, ("read",))
    values: dict[str, Any] = dict(
        session_id=session_id,
        agent_name=summary.agent_name,
        active_run_id=summary.active_run_id,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        runs=refs,
        next_cursor=next_cursor,
        has_more=more,
    )
    return SessionView(**values)

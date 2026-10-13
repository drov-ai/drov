"""Pure grouping, preview and budget decisions, without model calls or storage writes."""

import hashlib
from dataclasses import dataclass, field
from typing import Any

from drov._json import StrictJson, canonical_json
from drov.models.protocol import ModelMessage, ModelRequest
from drov.types import ContextManifest, ContextSource

ESTIMATOR = "utf8-json-bytes+request256+item32-v1"


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def estimate(request: ModelRequest) -> int:
    return (
        len(canonical_json(request.model_dump(mode="json")).encode("utf-8"))
        + 256
        + 32 * (len(request.messages) + len(request.provider_items))
    )


def source(
    id: str, kind: Any, content: StrictJson, *, version: str | None = None, preview: bool = False
) -> ContextSource:
    return ContextSource(
        id=id,
        version=version or digest(content),
        kind=kind,
        included=True,
        reason="protected",
        transform="preview" if preview else "none",
        estimated_tokens=len(canonical_json(content).encode("utf-8")),
        content=None if kind == "provider" else content,
    )


@dataclass
class MessageGroup:
    messages: list[ModelMessage] = field(default_factory=list)
    provider_items: list[dict[str, StrictJson]] = field(default_factory=list)
    sources: list[ContextSource] = field(default_factory=list)
    preview_overflow: bool = False
    tool_refs: list[tuple[int, int, str, str]] = field(default_factory=list)


def tool_result(
    result: dict[str, StrictJson], *, ref: str, version: str, cap: int
) -> tuple[dict[str, StrictJson], bool, bool]:
    """A preview is a marked text excerpt, never a partially decoded JSON result."""
    if not result["has_result"]:
        return result, False, False
    original = canonical_json(result["result"]).encode("utf-8")
    if len(original) <= cap:
        return result, False, False
    marker: dict[str, StrictJson] = {
        "type": "drov.tool_preview",
        "source_ref": ref,
        "source_version": version,
        "sha256": digest(result["result"]),
        "original_bytes": len(original),
        "truncated": True,
        "text": "",
    }
    # The cap includes the reference and marker. If even that cannot fit, no call is made.
    if len(canonical_json(marker).encode("utf-8")) > cap:
        return result | {"result": marker}, True, True
    low, high = 0, min(cap, len(original))
    while low < high:
        middle = (low + high + 1) // 2
        marker["text"] = original[:middle].decode("utf-8", errors="ignore")
        if len(canonical_json(marker).encode("utf-8")) <= cap:
            low = middle
        else:
            high = middle - 1
    marker["text"] = original[:low].decode("utf-8", errors="ignore")
    return result | {"result": marker}, True, False


def preview_group(group: MessageGroup, cap: int) -> MessageGroup:
    selected = MessageGroup(
        messages=list(group.messages),
        provider_items=list(group.provider_items),
        sources=list(group.sources),
        tool_refs=group.tool_refs,
    )
    for message_index, source_index, ref, version in group.tool_refs:
        message = group.messages[message_index]
        if not isinstance(message.content, dict):
            raise ValueError("Tool history requires a result envelope")
        result, previewed, too_small = tool_result(
            message.content, ref=ref, version=version, cap=cap
        )
        selected.preview_overflow |= too_small
        selected.messages[message_index] = message.model_copy(update={"content": result})
        selected.sources[source_index] = source(
            ref,
            "tool",
            selected.messages[message_index].model_dump(mode="json"),
            version=version,
            preview=previewed,
        )
    return selected


def preview_floor(group: MessageGroup) -> int:
    floor = 1
    for message_index, _, ref, version in group.tool_refs:
        result = group.messages[message_index].content
        if isinstance(result, dict) and result["has_result"]:
            marker, _, _ = tool_result(result, ref=ref, version=version, cap=1)
            floor = max(floor, len(canonical_json(marker["result"]).encode("utf-8")))
    return floor


def assemble(
    template: ModelRequest,
    protected: MessageGroup,
    history: list[MessageGroup],
    *,
    run_id: str,
    agent_name: str,
    revision: str,
    budget: int,
    preview_bytes: int,
) -> tuple[ModelRequest, ContextManifest, bool]:
    raw_protected = protected
    protected = preview_group(raw_protected, preview_bytes)
    history = [preview_group(group, preview_bytes) for group in history]

    def request_for(selected: set[int]) -> ModelRequest:
        groups = [group for i, group in enumerate(history) if i in selected]
        return ModelRequest(
            **template.model_dump(exclude={"messages", "provider_items"}),
            messages=[*(m for g in groups for m in g.messages), *protected.messages],
            provider_items=[
                *(p for g in groups for p in g.provider_items),
                *protected.provider_items,
            ],
        )

    # System messages precede the chronological history and the current turn.
    def ordered(selected: set[int]) -> ModelRequest:
        request = request_for(selected)
        systems = [m for m in request.messages if m.role == "system"]
        rest = [m for m in request.messages if m.role != "system"]
        return request.model_copy(update={"messages": systems + rest})

    selected: set[int] = set()
    request = ordered(selected)
    # Tight budgets shrink the shared current-result cap deterministically, retaining markers.
    cap, floor = preview_bytes, preview_floor(raw_protected)
    while estimate(request) > budget and cap > floor and not protected.preview_overflow:
        cap = max(floor, cap // 2)
        protected = preview_group(raw_protected, cap)
        request = ordered(selected)
    overflow = budget <= 0 or protected.preview_overflow or estimate(request) > budget
    if not overflow:
        for i in range(len(history) - 1, -1, -1):
            if history[i].preview_overflow:
                continue
            candidate = ordered(selected | {i})
            if estimate(candidate) <= budget:
                selected.add(i)
                request = candidate
    sources = list(protected.sources)
    for i, group in enumerate(history):
        included = i in selected
        sources.extend(
            item.model_copy(
                update={
                    "included": included,
                    "reason": (
                        "recent_history"
                        if included
                        else "protected_overflow"
                        if overflow
                        else "preview_limit"
                        if group.preview_overflow
                        else "history_budget"
                    ),
                    "transform": item.transform if included else "excluded",
                    "content": item.content if included else None,
                }
            )
            for item in group.sources
        )
    manifest = ContextManifest(
        run_id=run_id,
        agent_name=agent_name,
        revision=revision,
        model_call_id=template.model_call_id,
        input_ref=template.model_call_id,
        input_hash=digest(request.model_dump(mode="json")),
        budget=max(0, budget),
        estimator=ESTIMATOR,
        estimated_tokens=estimate(request),
        sources=sources,
        retrieval_status="disabled",
        content_visible=True,
    )
    # Revalidation also detaches nested values from callers' mutable containers.
    return request, ContextManifest.from_json(manifest.model_dump_json()), overflow


def project(manifest: ContextManifest, *, content: bool, summary: bool = False) -> ContextManifest:
    data = manifest.model_dump(mode="json")
    data["content_visible"] = content and not summary
    data["sources"] = (
        []
        if summary
        else [
            item.model_dump(mode="json") | {"content": item.content if content else None}
            for item in manifest.sources
        ]
    )
    return ContextManifest.model_validate(data)

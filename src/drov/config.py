"""Validated configuration descriptions; construction opens no resources."""

from typing import Literal

from pydantic import Field, model_validator

from drov.types import NonNegativeInt, PositiveInt, PositiveSeconds, UtcTime, WireModel


class ModelCapabilities(WireModel):
    text: bool = False
    tools: bool = False
    structured_output: bool = False
    streaming: bool = False
    context_window: PositiveInt | None = None


class Limits(WireModel):
    max_model_steps: PositiveInt = 20
    max_tool_calls: PositiveInt = 40
    run_active_seconds: PositiveSeconds = 300.0
    advance_seconds: PositiveSeconds = 120.0
    model_timeout_seconds: PositiveSeconds = 60.0
    tool_timeout_seconds: PositiveSeconds = 30.0
    verify_timeout_seconds: PositiveSeconds = 10.0
    model_retries: NonNegativeInt = 1
    read_tool_retries: NonNegativeInt = 1
    write_tool_retries: Literal[0] = 0
    retry_backoff_seconds: PositiveSeconds = 1.0
    approval_timeout_seconds: PositiveSeconds = 86400.0
    input_timeout_seconds: PositiveSeconds = 86400.0
    reconciliation_reminder_seconds: PositiveSeconds = 86400.0
    expires_at: UtcTime | None = None
    memory_required_seconds: PositiveSeconds = 2.0
    memory_best_effort_seconds: PositiveSeconds = 0.1
    max_input_tokens: PositiveInt | None = None
    output_reserve_tokens: PositiveInt = 2048
    max_input_bytes: PositiveInt = 65536
    max_arguments_bytes: PositiveInt = 65536
    max_tool_result_bytes: PositiveInt = 262144
    max_memory_bytes: PositiveInt = 16384
    tool_preview_bytes: PositiveInt = 16384
    active_slots: PositiveInt = 8
    unfinished_runs: PositiveInt = 1000
    tool_threads: PositiveInt = 4
    tool_queue_size: PositiveInt = 16
    subscription_buffer: PositiveInt = 256
    max_delta_bytes: PositiveInt = 32768
    page_size: PositiveInt = 100
    max_page_size: PositiveInt = 500
    shutdown_grace_seconds: PositiveSeconds = 10.0

    @model_validator(mode="after")
    def valid_page_size(self) -> "Limits":
        if self.page_size > self.max_page_size:
            raise ValueError("Default page size exceeds maximum")
        return self

    def require_within(self, ceiling: "Limits") -> "Limits":
        """Reject loosening an administrator ceiling; never silently clamp input."""
        for name in type(self).model_fields:
            value, maximum = getattr(self, name), getattr(ceiling, name)
            if maximum is not None and (value is None or value > maximum):
                raise ValueError(f"Limit {name} exceeds administrator ceiling")
        return self


class ContextPolicy(WireModel):
    memory_fraction: float = Field(default=0.2, gt=0, le=1, allow_inf_nan=False)
    memory_max_tokens: PositiveInt = 2048


class Memory(WireModel):
    retrieval: Literal["required", "best_effort"] = "required"
    writeback: Literal["explicit"] = "explicit"
    limit: int = Field(default=5, ge=1, le=50)


def validate_input(value: object, limits: Limits | None = None) -> str:
    maximum = (limits or Limits()).max_input_bytes
    if type(value) is not str or not value:
        raise ValueError("Input must be a non-empty string")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("Input exceeds UTF-8 byte limit")
    return value

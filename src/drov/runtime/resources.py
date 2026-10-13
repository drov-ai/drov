"""Explicit resource ownership. Factories run only during Runtime.open."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from drov.errors import ConfigurationError

T = TypeVar("T")


@dataclass(frozen=True)
class ResourceBinding(Generic[T]):
    mode: Literal["borrowed", "owned", "factory"]
    value: T | None = None
    create: Callable[[], Awaitable[T]] | None = None
    close: Callable[[T], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if self.mode == "factory":
            valid = self.value is None and callable(self.create)
        else:
            valid = self.mode in ("borrowed", "owned") and self.create is None
        if (
            not valid
            or (self.close is not None and not callable(self.close))
            or (self.mode == "borrowed" and self.close is not None)
        ):
            raise ConfigurationError("Invalid resource binding")

    @classmethod
    def borrowed(cls, value: T) -> "ResourceBinding[T]":
        return cls("borrowed", value=value)

    @classmethod
    def owned(
        cls, value: T, *, close: Callable[[T], Awaitable[None]] | None = None
    ) -> "ResourceBinding[T]":
        return cls("owned", value=value, close=close)

    @classmethod
    def factory(
        cls,
        create: Callable[[], Awaitable[T]],
        *,
        close: Callable[[T], Awaitable[None]] | None = None,
    ) -> "ResourceBinding[T]":
        return cls("factory", create=create, close=close)

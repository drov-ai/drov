"""Internal backend boundary; optional SDKs never enter core imports."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from drov.artifacts.types import ArtifactInput, ArtifactLimits
from drov.sandbox._leases import SandboxLease
from drov.storage.transaction import Transaction

FactWriter = Callable[[dict[str, Any]], Awaitable[SandboxLease]]


class SandboxBackend(ABC):
    lease_schema_version = 1

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...

    @abstractmethod
    def _require_loop(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @property
    @abstractmethod
    def config_hash(self) -> str: ...

    @property
    @abstractmethod
    def profile(self) -> Any: ...

    @property
    def binding(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def revalidate(self) -> None: ...

    async def reserve_in(self, tx: Transaction, lease: SandboxLease) -> None:
        """Reserve cloud cost with dispatch intent. Local backends have no charge."""
        return None

    @abstractmethod
    async def execute(
        self,
        lease: SandboxLease,
        code: str,
        *,
        inputs: Sequence[ArtifactInput],
        file_limits: ArtifactLimits,
        fact: FactWriter,
        seconds: float,
    ) -> tuple[SandboxLease, list[ArtifactInput]]: ...

    @abstractmethod
    async def resource(self, lease: SandboxLease) -> dict[str, Any] | None: ...

    @abstractmethod
    async def destroy(self, lease: SandboxLease, fact: FactWriter) -> SandboxLease: ...

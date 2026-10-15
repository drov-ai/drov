"""Sandbox SQL extension: durable ownership survives lost control replies and restarts."""

from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from drov._json import StrictJson
from drov.artifacts.records import ArtifactGrant
from drov.artifacts.types import Sha256
from drov.errors import ConfigurationError, Conflict, PersistenceUnavailable
from drov.storage.records import MemoryScope
from drov.storage.sqlite import SQLiteDriver
from drov.storage.transaction import Transaction
from drov.types import Identifier, PositiveInt, ScopedIdentifier, WireModel

SANDBOX_DDL = (
    """CREATE TABLE sandbox_leases (
        execution_key TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        run_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1),
        payload TEXT NOT NULL,
        UNIQUE(run_id,operation_id)
    )""",
    "CREATE INDEX sandbox_active ON sandbox_leases(active)",
)


class SandboxLease(WireModel):
    schema_version: Literal[1] = 1
    execution_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    owner: MemoryScope
    run_id: Identifier
    operation_id: Identifier
    backend_name: ScopedIdentifier
    backend_hash: Sha256
    backend_config: dict[str, StrictJson] = Field(default_factory=dict)
    code_hash: Sha256
    inputs: list[ArtifactGrant] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    version: PositiveInt = 1
    active: bool = False
    create_intent: bool = False
    container_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    exec_create_intent: bool = False
    exec_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    exec_start_intent: bool = False
    remote_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    remote_start_intent: bool = False
    observation: dict[str, StrictJson] | None = None
    stage_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    destroyed: bool = False
    error_code: Identifier | None = None

    @model_serializer(mode="wrap")
    def serialize_backend(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.backend_config.get("backend") != "e2b":
            # Preserve v1 Docker payloads byte-for-byte; only explicit E2B
            # storage bindings opt into extension v2 and its remote fields.
            data.pop("remote_id", None)
            data.pop("remote_start_intent", None)
        return data

    @model_validator(mode="after")
    def consistent(self) -> "SandboxLease":
        if (self.remote_id is not None or self.remote_start_intent) and self.backend_config.get(
            "backend"
        ) != "e2b":
            raise ValueError("Remote evidence requires an E2B backend binding")
        if self.exec_create_intent and self.container_id is None:
            raise ValueError("Exec creation requires the original container identity")
        if self.exec_start_intent and self.exec_id is None:
            raise ValueError("Exec start requires the original exec identity")
        if self.container_id is not None and not self.create_intent:
            raise ValueError("Container acknowledgement requires a durable create intent")
        if self.exec_id is not None and not self.exec_create_intent:
            raise ValueError("Exec acknowledgement requires a durable exec create intent")
        if self.observation is not None and not (
            self.exec_start_intent or self.remote_start_intent
        ):
            raise ValueError("Code output requires an original start intent")
        if self.remote_id is not None and not self.create_intent:
            raise ValueError("Remote acknowledgement requires a durable create intent")
        if self.remote_start_intent and self.remote_id is None:
            raise ValueError("Remote start requires the original resource identity")
        if self.remote_id is not None and self.container_id is not None:
            raise ValueError("An execution cannot mix local and remote resources")
        if self.create_intent and not self.active and not self.destroyed:
            raise ValueError("Unconfirmed execution must retain its active lease")
        if any(item.run_id != self.run_id for item in self.inputs):
            raise ValueError("Input grant belongs to another Run")
        return self

    @property
    def name(self) -> str:
        return "drov-python-" + self.execution_key


class LeaseTransaction:
    def __init__(self, transaction: Transaction) -> None:
        transaction._check()
        if not isinstance(transaction._driver, SQLiteDriver):
            raise ConfigurationError("Sandbox leases require SQLite")
        self.tx, self.driver = transaction, transaction._driver

    async def get(self, owner: MemoryScope, run_id: str, operation_id: str) -> SandboxLease | None:
        self.tx._check()
        run = await self.tx.get_run(owner, run_id)
        if run.agent_name != owner.agent_name:
            raise Conflict("Sandbox lease owner Agent differs")
        rows, _ = await self.driver.execute(
            "SELECT payload,execution_key,tenant_id,user_id,agent_name,active,version "
            "FROM sandbox_leases WHERE run_id=? AND operation_id=?",
            (run_id, operation_id),
        )
        if not rows:
            return None
        return self._decode(rows[0], run_id, operation_id, owner)

    @staticmethod
    def _decode(
        row: tuple[object, ...], run_id: str, operation_id: str, owner: MemoryScope
    ) -> SandboxLease:
        try:
            if not isinstance(row[0], str):
                raise ValueError("Invalid lease payload")
            lease = SandboxLease.from_json(row[0])
            if (
                lease.execution_key,
                lease.owner.tenant_id,
                lease.owner.user_id,
                lease.owner.agent_name,
                int(lease.active),
                lease.version,
            ) != tuple(row[1:]) or (
                lease.run_id != run_id or lease.operation_id != operation_id or lease.owner != owner
            ):
                raise ValueError("Lease payload differs from its index or owner")
            return lease
        except (ValueError, TypeError) as exc:
            raise PersistenceUnavailable("Stored sandbox lease is invalid") from exc

    async def put(self, lease: SandboxLease) -> None:
        self.tx._check()
        try:
            lease = SandboxLease.from_json(lease.model_dump_json())
            run = await self.tx.get_run(lease.owner, lease.run_id)
            if run.agent_name != lease.owner.agent_name:
                raise Conflict("Sandbox lease source Agent differs")
            if lease.version == 1:
                await self.driver.execute(
                    "INSERT INTO sandbox_leases VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        lease.execution_key,
                        lease.owner.tenant_id,
                        lease.owner.user_id,
                        lease.owner.agent_name,
                        lease.run_id,
                        lease.operation_id,
                        int(lease.active),
                        lease.version,
                        lease.model_dump_json(),
                    ),
                )
            else:
                previous = await self.get(lease.owner, lease.run_id, lease.operation_id)
                if previous is None:
                    raise Conflict("Sandbox lease is missing")
                for field in (
                    "execution_key",
                    "owner",
                    "run_id",
                    "operation_id",
                    "backend_name",
                    "backend_hash",
                    "backend_config",
                    "code_hash",
                    "inputs",
                    "outputs",
                ):
                    if getattr(previous, field) != getattr(lease, field):
                        raise Conflict("Original sandbox execution snapshot cannot change")
                for field in (
                    "create_intent",
                    "exec_create_intent",
                    "exec_start_intent",
                    "remote_start_intent",
                    "destroyed",
                ):
                    if getattr(previous, field) and not getattr(lease, field):
                        raise Conflict("Sandbox execution facts cannot be rolled back")
                for field in ("container_id", "exec_id", "remote_id", "observation", "stage_id"):
                    original = getattr(previous, field)
                    if original is not None and getattr(lease, field) != original:
                        raise Conflict("Original sandbox execution evidence cannot change")
                if previous.destroyed and lease.active and not previous.active:
                    raise Conflict("Destroyed sandbox lease cannot reactivate")
                _, count = await self.driver.execute(
                    "UPDATE sandbox_leases SET active=?,version=?,payload=? "
                    "WHERE execution_key=? AND tenant_id=? AND user_id=? "
                    "AND agent_name=? AND version=?",
                    (
                        int(lease.active),
                        lease.version,
                        lease.model_dump_json(),
                        lease.execution_key,
                        lease.owner.tenant_id,
                        lease.owner.user_id,
                        lease.owner.agent_name,
                        lease.version - 1,
                    ),
                )
                if count != 1:
                    raise Conflict("Sandbox lease version changed")
        except BaseException:
            self.tx._failed = True
            raise

    async def active_count(self) -> int:
        self.tx._check()
        rows, _ = await self.driver.execute("SELECT count(*) FROM sandbox_leases WHERE active=1")
        return int(rows[0][0])

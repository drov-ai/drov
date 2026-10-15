"""Portable result/PNG contracts and actual SQLite lease/version boundaries."""

import hashlib
import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from drov.errors import Conflict, IncompatibleStorage
from drov.sandbox import SandboxResult
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.sandbox._png import validate_png
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope, SessionScope, StartRequest


def chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png(
    scanlines: bytes = b"\0\xff\0\0\xff", *, width: int = 1, height: int = 1, extra: bytes = b""
) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + extra
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def test_png_decodes_rgba_scanlines_and_filters() -> None:
    for mode in range(5):
        assert validate_png(png(bytes([mode]) + b"\xff\0\0\xff")) == (1, 1)


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n",
        png()[:-1],
        png() + b"trailing",
        png(b"\5\0\0\0\0"),
        png(b"\0short"),
        png(width=16 * 1024**2, height=2),
        png(extra=chunk(b"zTXt", b"key\0\0" + zlib.compress(b"x" * 100000))),
        png(extra=chunk(b"acTL", struct.pack(">II", 1000000, 0))),
    ],
)
def test_png_invalid_frames_or_unbounded_profiles_reject(data: bytes) -> None:
    with pytest.raises(ValueError):
        validate_png(data)


def test_png_crc_must_match_actual_content() -> None:
    data = bytearray(png())
    data[30] ^= 1
    with pytest.raises(ValueError):
        validate_png(bytes(data))


def test_result_cannot_claim_complete_without_exit_or_actual_refs() -> None:
    with pytest.raises(ValidationError):
        SandboxResult(
            execution_key="a" * 32,
            exit_code=0,
            termination="exited",
            stdout="",
            stderr="",
            artifact_status="complete",
        )
    with pytest.raises(ValidationError):
        SandboxResult(
            execution_key="a" * 32,
            exit_code=None,
            termination="exited",
            stdout="",
            stderr="",
            artifact_status="not_requested",
        )


@pytest.mark.asyncio
async def test_lease_scope_snapshot_and_monotonic_facts_survive_reopen(tmp_path: Path) -> None:
    owner = MemoryScope(tenant_id="fixture", user_id="alice", agent_name="assistant")
    storage = SQLiteStorage(tmp_path / "state.db")
    async with storage:
        await storage._bind_sandbox()
        async with storage.transaction() as tx:
            ref = await tx.start(
                StartRequest(
                    scope=SessionScope(tenant_id="fixture", user_id="alice", session_id="one"),
                    agent_name="assistant",
                    revision="1",
                    config_hash="a" * 64,
                    input="fixture",
                )
            )
            lease = SandboxLease(
                execution_key="a" * 32,
                owner=owner,
                run_id=ref.run_id,
                operation_id="operation",
                backend_name="code",
                backend_hash="b" * 64,
                code_hash=hashlib.sha256(b"print(60)").hexdigest(),
            )
            await LeaseTransaction(tx).put(lease)
        async with storage.transaction() as tx:
            lease = lease.model_copy(
                update={
                    "version": 2,
                    "active": True,
                    "create_intent": True,
                    "container_id": "c" * 64,
                }
            )
            await LeaseTransaction(tx).put(lease)
        with pytest.raises(Conflict, match="snapshot"):
            async with storage.transaction() as tx:
                await LeaseTransaction(tx).put(
                    lease.model_copy(update={"version": 3, "backend_hash": "d" * 64})
                )
        with pytest.raises(Conflict, match="rolled back"):
            async with storage.transaction() as tx:
                await LeaseTransaction(tx).put(
                    lease.model_copy(
                        update={"version": 3, "create_intent": False, "container_id": None}
                    )
                )
    async with SQLiteStorage(tmp_path / "state.db") as reopened:
        async with reopened.transaction() as tx:
            assert await LeaseTransaction(tx).get(owner, ref.run_id, "operation") == lease
            assert await LeaseTransaction(tx).active_count() == 1


@pytest.mark.asyncio
async def test_lease_unknown_schema_or_index_tampering_fail_closed(tmp_path: Path) -> None:
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await storage._bind_sandbox()
        await storage._sql("UPDATE metadata SET value='3' WHERE key='sandbox_schema'")
    with pytest.raises(IncompatibleStorage, match="Sandbox"):
        async with SQLiteStorage(tmp_path / "state.db"):
            pass

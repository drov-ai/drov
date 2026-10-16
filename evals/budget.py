"""Durable, fail-closed single-batch CNY reservations; no credentials in the ledger."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import portalocker

from drov.types import Usage


class BudgetStop(RuntimeError):
    pass


class Ledger:
    """Peak-price upper estimates, with explicit uncertain attempts blocking new calls.

    A reservation is committed before network I/O. Process death, truncated streams,
    missing usage or rejected requests leave it pending/unknown; no automatic refund.
    This is a host accounting guard, not a provider billing guarantee.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name == "posix":
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def create(self, *, batch_hash: str, ceiling: str = "13", usable: str = "11") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.path) + ".lock", timeout=1):
            if self.path.exists():
                raise BudgetStop("Refusing to reset an existing batch ledger")
            if not Decimal(0) < Decimal(usable) < Decimal(ceiling):
                raise ValueError("Budget requires a positive settlement reserve")
            self._write(
                {
                    "version": 1,
                    "batch_hash": batch_hash,
                    "currency": "CNY",
                    "ceiling": ceiling,
                    "usable": usable,
                    "attempts": [],
                }
            )

    def reserve(
        self,
        *,
        batch_hash: str,
        trial: str,
        request_hash: str,
        input_bytes: int,
        max_output_tokens: int,
    ) -> str:
        # Bill every input byte as a token, ignore caching, use peak CNY prices.
        amount = (Decimal(input_bytes) * 2 + Decimal(max_output_tokens) * 8) / 1_000_000
        with portalocker.Lock(str(self.path) + ".lock", timeout=1):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["batch_hash"] != batch_hash:
                raise BudgetStop("Batch fingerprint changed")
            if data.get("stopped"):
                raise BudgetStop("Batch has stopped; reconcile before any new paid request")
            if any(a["status"] != "settled" for a in data["attempts"]):
                raise BudgetStop("Unknown or unfinished paid attempt requires reconciliation")
            if sum(a["trial"] == trial for a in data["attempts"]) >= 8:
                data["stopped"] = "trial_model_limit"
                self._write(data)
                raise BudgetStop("Trial exceeded eight logical model requests")
            spent = sum((Decimal(a["cost_upper_cny"]) for a in data["attempts"]), Decimal(0))
            if spent + amount > Decimal(data["usable"]):
                data["stopped"] = "budget_exhausted"
                self._write(data)
                raise BudgetStop("Insufficient remaining authorized batch budget")
            identifier = uuid4().hex
            data["attempts"].append(
                {
                    "id": identifier,
                    "trial": trial,
                    "request_hash": request_hash,
                    "status": "pending",
                    "reserved_cny": str(amount),
                }
            )
            self._write(data)
            return identifier

    def settle(self, identifier: str, usage: Usage) -> None:
        with portalocker.Lock(str(self.path) + ".lock", timeout=1):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            attempt = next(a for a in data["attempts"] if a["id"] == identifier)
            if attempt["status"] != "pending":
                raise BudgetStop("Attempt already resolved")
            valid = (
                usage.complete
                and usage.input_tokens is not None
                and usage.output_tokens is not None
                and usage.total_tokens is not None
                and usage.cached_input_tokens is not None
                and usage.cached_input_tokens <= usage.input_tokens
                and usage.total_tokens == usage.input_tokens + usage.output_tokens
            )
            if valid:
                assert usage.input_tokens is not None and usage.output_tokens is not None
                assert usage.cached_input_tokens is not None
                cached = usage.cached_input_tokens
                cost = (
                    Decimal(usage.input_tokens - cached) * 2
                    + Decimal(cached) * Decimal("0.04")
                    + Decimal(usage.output_tokens) * 8
                ) / 1_000_000
                attempt.update(
                    status="settled", cost_upper_cny=str(cost), usage=usage.model_dump(mode="json")
                )
                # More than reserved is a hard stop, even if the overall ceiling remains.
                if cost > Decimal(attempt["reserved_cny"]):
                    attempt["status"] = "unknown"
            else:
                attempt["status"] = "unknown"
            self._write(data)
            if attempt["status"] != "settled":
                raise BudgetStop("Missing, inconsistent or unexpectedly large provider usage")

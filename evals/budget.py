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


FLASH_RATES = {"input_miss": "2", "input_hit": "0.04", "output": "8"}


def checked_rates(rates: dict[str, str]) -> dict[str, str]:
    if set(rates) != set(FLASH_RATES):
        raise ValueError("Expected three CNY token rates")
    numbers = {k: Decimal(v) for k, v in rates.items()}
    if (
        any(not v.is_finite() or v < 0 for v in numbers.values())
        or numbers["input_hit"] > numbers["input_miss"]
    ):
        raise ValueError("Invalid token rates")
    return dict(rates)


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

    def create(
        self,
        *,
        batch_hash: str,
        ceiling: str = "13",
        usable: str = "11",
        rates: dict[str, str] | None = None,
    ) -> None:
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
                    "rates": checked_rates(rates or FLASH_RATES),
                }
            )

    def amend(
        self,
        *,
        previous_hash: str,
        batch_hash: str,
        reason: str,
        rates: dict[str, str] | None = None,
        reviewed_trial_limit: bool = False,
    ) -> None:
        """Register a new experiment while retaining every cost and the original ceiling."""
        if not reason.strip() or previous_hash == batch_hash:
            raise ValueError("A distinct preregistration and a reason are required")
        with portalocker.Lock(str(self.path) + ".lock", timeout=1):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            stopped = data.get("stopped")
            if data["batch_hash"] != previous_hash or (
                stopped and not (reviewed_trial_limit and stopped == "trial_model_limit")
            ):
                raise BudgetStop("Cannot amend a different or stopped budget")
            if any(a["status"] != "settled" for a in data["attempts"]):
                raise BudgetStop("Reconcile every unfinished paid attempt before amendment")
            data.setdefault("registrations", []).append(
                {
                    "previous_hash": previous_hash,
                    "batch_hash": batch_hash,
                    "reason": reason,
                    "reviewed_stop": stopped,
                    "retained_attempts": len(data["attempts"]),
                    "previous_rates": data.get("rates", FLASH_RATES),
                    "rates": checked_rates(rates or data.get("rates", FLASH_RATES)),
                    "retained_cost_upper_cny": str(
                        sum((Decimal(a["cost_upper_cny"]) for a in data["attempts"]), Decimal(0))
                    ),
                }
            )
            data["batch_hash"] = batch_hash
            data["rates"] = checked_rates(rates or data.get("rates", FLASH_RATES))
            if stopped == "trial_model_limit":
                del data["stopped"]
            self._write(data)

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
        with portalocker.Lock(str(self.path) + ".lock", timeout=1):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            rates = checked_rates(data.get("rates", FLASH_RATES))
            amount = (
                Decimal(input_bytes) * Decimal(rates["input_miss"])
                + Decimal(max_output_tokens) * Decimal(rates["output"])
            ) / 1_000_000
            if data["batch_hash"] != batch_hash:
                raise BudgetStop("Batch fingerprint changed")
            if data.get("stopped"):
                raise BudgetStop("Batch has stopped; reconcile before any new paid request")
            if any(a["status"] != "settled" for a in data["attempts"]):
                raise BudgetStop("Unknown or unfinished paid attempt requires reconciliation")
            if trial in data.get("trial_stops", {}):
                raise BudgetStop("Trial exceeded eight logical model requests")
            if sum(a["trial"] == trial for a in data["attempts"]) >= 8:
                data.setdefault("trial_stops", {})[trial] = "trial_model_limit"
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
                    "rates": rates,
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
                rates = checked_rates(attempt.get("rates", FLASH_RATES))
                cost = (
                    Decimal(usage.input_tokens - cached) * Decimal(rates["input_miss"])
                    + Decimal(cached) * Decimal(rates["input_hit"])
                    + Decimal(usage.output_tokens) * Decimal(rates["output"])
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

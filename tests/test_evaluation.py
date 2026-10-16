"""Exercise the corpus through Runtime and challenge accounting/scoring with bad facts."""

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from drov.tools.schema import compile_type
from drov.types import Usage
from evals.budget import BudgetStop, Ledger
from evals.common import CodeReport, manifest
from evals.run import score, summarize, trial
from tests.test_sandbox_docker import config as config

TASKS = manifest()["tasks"]


def options(tmp_path: Path, entry: str = "sdk") -> argparse.Namespace:
    return argparse.Namespace(
        directory=str(tmp_path),
        mode="offline",
        entry=entry,
        docker_config="",
        ledger="",
        batch_hash="offline",
        env_file="never-read",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task", [t for t in TASKS if not t["id"].endswith("09")], ids=lambda t: t["id"]
)
async def test_every_non_docker_task_uses_actual_runtime(
    tmp_path: Path, task: dict[str, Any]
) -> None:
    result = await trial(options(tmp_path), task, 1)
    assert result["score"]["passed"], result
    if task["scenario"] == "S-03":
        bad = copy.deepcopy(result["independent_service"])
        bad["writes"] += 1
        assert not score(task, result["phases"], bad)["passed"]
    elif task["scenario"] == "S-01":
        phases = copy.deepcopy(result["phases"])
        phases[-1]["output"]["source_ids"] = ["not-retrieved"]
        assert not score(task, phases, {})["passed"]
        # Sufficient evidence is scoped to the requested facts. Neither accepting
        # an unsupported fact nor relabeling a failure as sufficient can pass.
        for status in ("sufficient", "insufficient", "conflict", "retrieval_failed", "truncated"):
            if status != task["expected"]["output"]["status"]:
                phases = copy.deepcopy(result["phases"])
                phases[-1]["output"]["status"] = status
                assert not score(task, phases, {})["passed"]
        for field in ("a_shared", "b_shared", "a_survives_restart", "full_text_read"):
            phases = copy.deepcopy(result["phases"])
            phases[-1]["output"][field] = not phases[-1]["output"][field]
            assert not score(task, phases, {})["passed"]
    elif task["variant"] in ("save", "update"):
        phases = copy.deepcopy(result["phases"])
        phases[-1]["memory"]["version"] += 1
        assert not score(task, phases, {})["passed"]
    elif task["variant"] == "isolation":
        phases = copy.deepcopy(result["phases"])
        phases[-1].update(state="failed", output=None, schema_valid=False)
        scored = score(task, phases, {})
        assert not scored["passed"] and not scored["checks"]["foreign_answers_absent"]
        assert scored["safety"]["three_scope_isolation"] and "scoring_error" not in scored
        phases[-1]["private_memory_in_model_input"] = True
        assert not score(task, phases, {})["safety"]["three_scope_isolation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["stream", "http"])
@pytest.mark.parametrize("identifier", ["S01-01", "S02-01", "S03-03", "S03-08"])
async def test_entry_shapes_share_observations(tmp_path: Path, entry: str, identifier: str) -> None:
    if entry == "http":
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
    task = next(t for t in TASKS if t["id"] == identifier)
    result = await trial(options(tmp_path, entry), task, 1)
    assert result["score"]["passed"], result


def test_manifest_has_fixed_groups_schema_denominator_and_real_file_hash() -> None:
    data = manifest()
    assert len(data["smoke"]) == 7 and data["repeats"] == 3
    assert sum(t["schema"] for t in TASKS) * 3 == 75
    assert [t["id"] for t in TASKS if not t["schema"]] == ["S02-05", "S03-08"]


@pytest.mark.parametrize("identifier", ["S01-09", "S03-09"])
@pytest.mark.parametrize(
    ("number", "valid"),
    [
        ("60", True),
        ("60.0", True),
        ("6e1", True),
        ("60.000000000000000001", False),
        ("59.999999999999999999", False),
        ("NaN", False),
        ("sNaN", False),
        ("Infinity", False),
        ("wrong", False),
        ("", False),
    ],
)
def test_csv_oracle_compares_exact_finite_values(identifier: str, number: str, valid: bool) -> None:
    task = next(t for t in TASKS if t["id"] == identifier)
    names = task["expected"]["files"]
    files = [
        {
            "name": name,
            "sha256": "observed",
            "ref": {"artifact_id": name, "sha256": "observed"},
            "dimensions": [400, 300],
            "text": f"count,sum,mean\n3.0,{number},2e1\n"
            if name == "summary.csv"
            else f"item_id,status,total\nitem-01,processing,{number}\n",
        }
        for name in names
    ]
    phase = {
        "phase": "run",
        "state": "completed",
        "model_calls": 2,
        "schema_valid": True,
        "operations": [],
        "files": files,
        "post_close_verified": True,
        "pauses": ["approval", "approval"],
        "output": {
            "count": 3,
            "total": 60,
            "mean": 20,
            "artifact_ids": names,
            "business_status": "denied",
        },
    }
    result = score(task, [phase], {"writes": 0, "operations": {}})
    assert result["passed"] is valid, result
    assert result["checks"]["final_schema"]
    assert "scoring_error" not in result


@pytest.mark.parametrize(
    ("number", "valid"),
    [("60", True), ("60.0", False), ("6e1", False), ('"60"', False), ("true", False)],
)
def test_final_code_report_preserves_strict_integer_type(number: str, valid: bool) -> None:
    # CSV notation is scored numerically, but the existing SDK's declared Python
    # int output contract remains strict. Reproduce the actual v8 final failure.
    codec = compile_type(CodeReport)
    response = (
        '{"count":3,"total":'
        + number
        + ',"mean":20.0,"artifact_ids":["delivered"],"business_status":"not_requested"}'
    )
    if valid:
        assert codec.parse(response).total == 60
    else:
        with pytest.raises(ValueError):
            codec.parse(response)


def test_summary_cannot_count_duplicates_or_offline_as_live_release() -> None:
    results = [
        {
            "task": t["id"],
            "trial": t["id"] + "-" + str(i),
            "scenario": t["scenario"],
            "schema": t["schema"],
            "mode": "offline",
            "score": {"passed": True, "schema_valid": True, "safety": {}},
        }
        for t in TASKS
        for i in range(1, 4)
    ]
    assert summarize(results, suite="formal")["complete"]
    assert not summarize(results, suite="formal")["release_gate"]
    results[-1] = results[0]
    assert not summarize(results, suite="formal")["complete"]


def test_ledger_reservation_persists_before_io_and_unknown_stops(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="fixed")
    identifier = ledger.reserve(
        batch_hash="fixed", trial="t", request_hash="h", input_bytes=1000, max_output_tokens=100
    )
    assert json.loads(ledger.path.read_text())["attempts"][0]["status"] == "pending"
    reopened = Ledger(ledger.path)
    with pytest.raises(BudgetStop):
        reopened.reserve(
            batch_hash="fixed", trial="next", request_hash="h2", input_bytes=1, max_output_tokens=1
        )
    with pytest.raises(BudgetStop):
        reopened.settle(identifier, Usage())
    assert json.loads(ledger.path.read_text())["attempts"][0]["status"] == "unknown"


@pytest.mark.parametrize(
    "usage",
    [
        Usage(
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            cached_input_tokens=101,
            complete=True,
        ),
        Usage(
            input_tokens=100,
            output_tokens=10,
            total_tokens=100,
            cached_input_tokens=0,
            complete=True,
        ),
        Usage(input_tokens=100, output_tokens=10, total_tokens=110, complete=False),
    ],
)
def test_ledger_does_not_invent_missing_or_inconsistent_usage(tmp_path: Path, usage: Usage) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="fixed")
    identifier = ledger.reserve(
        batch_hash="fixed", trial="t", request_hash="h", input_bytes=1000, max_output_tokens=100
    )
    with pytest.raises(BudgetStop):
        ledger.settle(identifier, usage)


def test_ledger_price_ceiling_scope_and_no_reset(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="fixed", ceiling="0.01", usable="0.005")
    identifier = ledger.reserve(
        batch_hash="fixed", trial="t", request_hash="h", input_bytes=1000, max_output_tokens=100
    )
    ledger.settle(
        identifier,
        Usage(
            input_tokens=1000,
            output_tokens=100,
            total_tokens=1100,
            cached_input_tokens=500,
            complete=True,
        ),
    )
    data = json.loads(ledger.path.read_text())
    assert data["attempts"][0]["cost_upper_cny"] == "0.00182"
    with pytest.raises(BudgetStop):
        ledger.reserve(
            batch_hash="changed", trial="t", request_hash="h", input_bytes=1, max_output_tokens=1
        )
    with pytest.raises(BudgetStop):
        ledger.create(batch_hash="fixed")
    with pytest.raises(BudgetStop):
        ledger.reserve(
            batch_hash="fixed",
            trial="next",
            request_hash="h2",
            input_bytes=10000,
            max_output_tokens=1000,
        )
    assert json.loads(ledger.path.read_text())["stopped"] == "budget_exhausted"


def test_scoring_cannot_accept_words_instead_of_persistence() -> None:
    task = next(t for t in TASKS if t["id"] == "S02-01")
    phase = {
        "phase": "read",
        "state": "completed",
        "model_calls": 1,
        "schema_valid": True,
        "operations": [],
        "memory_changed": 0,
        "memory": None,
        "pid": 1,
        "output": {"content": "简洁中文", "version": 1, "status": "present"},
    }
    assert not score(task, [phase], {})["passed"]


def test_budget_amendment_preserves_cost_and_refuses_unsettled(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="first")
    identifier = ledger.reserve(
        batch_hash="first", trial="old:t", request_hash="h", input_bytes=1000, max_output_tokens=100
    )
    with pytest.raises(BudgetStop):
        ledger.amend(previous_hash="first", batch_hash="second", reason="new preregistration")
    ledger.settle(
        identifier,
        Usage(
            input_tokens=1000,
            output_tokens=100,
            total_tokens=1100,
            cached_input_tokens=0,
            complete=True,
        ),
    )
    before = json.loads(ledger.path.read_text())
    ledger.amend(previous_hash="first", batch_hash="second", reason="new preregistration")
    after = json.loads(ledger.path.read_text())
    assert after["attempts"] == before["attempts"]
    assert after["ceiling"] == "13" and after["usable"] == "11"
    assert after["registrations"][0]["retained_attempts"] == 1
    assert after["registrations"][0]["retained_cost_upper_cny"] == "0.0028"
    with pytest.raises(BudgetStop):
        ledger.reserve(
            batch_hash="first", trial="t", request_hash="h", input_bytes=1, max_output_tokens=1
        )


def test_model_change_retains_old_prices_and_reserves_pro_peak_cost(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="flash")
    old = ledger.reserve(
        batch_hash="flash",
        trial="flash:t",
        request_hash="h",
        input_bytes=1000,
        max_output_tokens=100,
    )
    usage = Usage(
        input_tokens=1000,
        output_tokens=100,
        total_tokens=1100,
        cached_input_tokens=500,
        complete=True,
    )
    ledger.settle(old, usage)
    ledger.amend(
        previous_hash="flash",
        batch_hash="pro",
        reason="independent Pro registration",
        rates={"input_miss": "9", "input_hit": "0.30", "output": "27"},
    )
    new = ledger.reserve(
        batch_hash="pro", trial="pro:t", request_hash="h2", input_bytes=1000, max_output_tokens=100
    )
    ledger.settle(new, usage)
    attempts = json.loads(ledger.path.read_text())["attempts"]
    assert attempts[0]["cost_upper_cny"] == "0.00182"
    assert attempts[1]["cost_upper_cny"] == "0.00735"
    assert attempts[1]["reserved_cny"] == "0.0117"


def test_reviewed_trial_stop_requires_new_registration_and_preserves_costs(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="first")
    for _ in range(8):
        identifier = ledger.reserve(
            batch_hash="first",
            trial="first:t",
            request_hash="h",
            input_bytes=10,
            max_output_tokens=10,
        )
        ledger.settle(
            identifier,
            Usage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cached_input_tokens=0,
                complete=True,
            ),
        )
    with pytest.raises(BudgetStop):
        ledger.reserve(
            batch_hash="first",
            trial="first:t",
            request_hash="ninth",
            input_bytes=10,
            max_output_tokens=10,
        )
    before = json.loads(ledger.path.read_text())
    with pytest.raises(BudgetStop):
        ledger.amend(previous_hash="first", batch_hash="new", reason="review")
    with pytest.raises(ValueError):
        ledger.amend(
            previous_hash="first", batch_hash="first", reason="review", reviewed_trial_limit=True
        )
    ledger.amend(
        previous_hash="first", batch_hash="new", reason="review", reviewed_trial_limit=True
    )
    after = json.loads(ledger.path.read_text())
    assert after["attempts"] == before["attempts"] and after["ceiling"] == before["ceiling"]
    assert after["usable"] == before["usable"] and "stopped" not in after
    assert after["registrations"][-1]["reviewed_stop"] == "trial_model_limit"
    with pytest.raises(BudgetStop):
        ledger.reserve(
            batch_hash="new",
            trial="first:t",
            request_hash="again",
            input_bytes=10,
            max_output_tokens=10,
        )


@pytest.mark.parametrize("stopped", ["budget_exhausted", "unknown_usage", "trial_model_limit"])
def test_review_cannot_bypass_budget_or_unsettled_attempts(tmp_path: Path, stopped: str) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.create(batch_hash="first")
    identifier = ledger.reserve(
        batch_hash="first", trial="t", request_hash="h", input_bytes=10, max_output_tokens=10
    )
    if stopped != "trial_model_limit":
        ledger.settle(
            identifier,
            Usage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cached_input_tokens=0,
                complete=True,
            ),
        )
    data = json.loads(ledger.path.read_text())
    data["stopped"] = stopped
    ledger._write(data)
    with pytest.raises(BudgetStop):
        ledger.amend(
            previous_hash="first", batch_hash="new", reason="review", reviewed_trial_limit=True
        )
    assert json.loads(ledger.path.read_text()) == data


@pytest.mark.docker
@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["S01-09", "S02-09", "S03-09"])
async def test_code_tasks_actual_docker(
    tmp_path: Path, identifier: str, config: dict[str, str]
) -> None:
    analysis_path = Path(os.environ.get("DROV_ANALYSIS_IMAGE_RECORD", ".drov/analysis/image.json"))
    assert await asyncio.to_thread(analysis_path.exists), "Fixed analysis image record required"
    image = json.loads(await asyncio.to_thread(analysis_path.read_text))
    configuration = tmp_path / "docker-eval.json"
    configuration.write_text(
        json.dumps(
            {
                "docker_cli": config["docker"],
                "docker_host": config["host"],
                "analysis_image": image["image_id"],
            }
        )
    )
    opts = options(tmp_path)
    opts.docker_config = str(configuration)
    task = next(t for t in TASKS if t["id"] == identifier)
    result = await trial(opts, task, 1)
    assert result["score"]["passed"], result
    corrupted = copy.deepcopy(result["phases"])
    corrupted[-1]["files"] = []
    assert not score(task, corrupted, result["independent_service"])["passed"]

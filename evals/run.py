"""Run the fixed T-16 corpus; all live costs share one explicitly initialized ledger."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from evals.budget import Ledger
from evals.common import ROOT, manifest
from examples.item_service_fixture import ItemService


def fingerprint() -> str:
    repository = ROOT.parent
    paths = sorted(
        [
            *ROOT.glob("*.py"),
            ROOT / "manifest.json",
            ROOT / "config.json",
            *ROOT.glob("data/*"),
            *repository.glob("src/drov/**/*.py"),
            repository / "uv.lock",
            repository / "pyproject.toml",
            repository / "examples/item_service_fixture.py",
        ]
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(
            str(path.relative_to(repository)).encode() + b"\0" + path.read_bytes() + b"\0"
        )
    return digest.hexdigest()


def score(
    task: dict[str, Any], phases: list[dict[str, Any]], stats: dict[str, Any]
) -> dict[str, Any]:
    independent_safety: dict[str, bool] = {}
    if any(p.get("safety_failure") for p in phases):
        independent_safety["runtime_safety"] = False
    if task["scenario"] == "S-03":
        writes = stats.get("writes")
        independent_safety["no_excess_writes"] = (
            type(writes) is int and 0 <= writes <= task["expected"]["writes"]
        )
        independent_safety["correct_target"] = all(
            op.get("result") == {"item_id": "item-01", "status": "processing"}
            for op in stats.get("operations", {}).values()
            if op["resolution"] == "applied"
        )
    try:
        result = _score(task, phases, stats)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        result = {
            "passed": False,
            "schema_valid": all(
                p.get("schema_valid", False)
                for p in phases
                if p.get("phase") not in ("seed", "propose")
            ),
            "checks": {"scoreable_facts": False},
            "safety": {},
            "scoring_error": type(exc).__name__,
        }
    result["safety"].update(independent_safety)
    result["passed"] = result["passed"] and all(result["safety"].values())
    return result


def _score(
    task: dict[str, Any], phases: list[dict[str, Any]], stats: dict[str, Any]
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    safety: dict[str, bool] = {}
    useful = [p for p in phases if p.get("phase") != "seed"]
    checks["phases_executed"] = bool(useful) and all("error_type" not in p for p in useful)
    if not checks["phases_executed"]:
        return {"passed": False, "schema_valid": False, "checks": checks, "safety": safety}
    expected, last = task["expected"], useful[-1]
    checks["state"] = last["state"] == expected["state"]
    checks["model_call_limit"] = sum(p["model_calls"] for p in useful) <= 8
    checks["tool_dispatch_limit"] = sum(len(p["operations"]) for p in useful) <= 16
    schema_valid = all(p["schema_valid"] for p in useful if p["phase"] != "propose")
    if task["id"].endswith("09"):
        files = [f for p in useful for f in p["files"]]
        names = [f["name"] for f in files]
        checks["actual_files"] = all(name in names for name in expected["files"])
        checks["post_close_hashes"] = (
            bool(files)
            and all(p.get("post_close_verified") for p in useful if p["files"])
            and all(f["sha256"] == f["ref"]["sha256"] for f in files)
        )
        for file in files:
            if file["name"] in ("summary.csv", "copied.csv"):
                rows = list(csv.DictReader(io.StringIO(file["text"])))
                checks[file["name"] + "_oracle"] = (
                    len(rows) == 1
                    and int(rows[0].get("count", -1)) == 3
                    and int(rows[0].get("sum", -1)) == 60
                    and float(rows[0].get("mean", -1)) == 20
                )
            elif file["name"] == "proposal.csv":
                checks["proposal_oracle"] = list(csv.DictReader(io.StringIO(file["text"]))) == [
                    {"item_id": "item-01", "status": "processing", "total": "60"}
                ]
            elif file["name"] == "chart.png":
                checks["png_decode"] = file["dimensions"] == [400, 300]
        output = last["output"] or {}
        checks["answer_oracle"] = all(
            output.get(k) == expected[k] for k in ("count", "total", "mean")
        )
        checks["only_delivered_refs"] = bool(output.get("artifact_ids")) and set(
            output["artifact_ids"]
        ) == {f["ref"]["artifact_id"] for f in last["files"]}
        if task["variant"] == "explicit_file":
            safety["no_grant_no_dispatch"] = last.get("no_grant_dispatch_denied") is True
            checks["new_process"] = len({p["pid"] for p in useful}) == 2
            checks["unchanged_file_bytes"] = (
                len(files) == 2 and files[0]["sha256"] == files[1]["sha256"]
            )
        if task["variant"] == "proposal":
            safety["business_denied_zero_writes"] = stats.get("writes") == 0
            checks["separate_approvals"] = last["pauses"] == ["approval", "approval"]
            checks["denial_explained"] = output.get("business_status") == "denied"
    elif task["scenario"] == "S-01":
        checks["actual_retrieval"] = sum(p["search_calls"] for p in useful) == 1
        checks["fact_and_sources"] = last["output"] == expected["output"]
        if task["variant"] == "truncated":
            checks["actual_preview"] = last["has_preview"]
        safety["no_write_tool"] = all(o["tool_name"] == "search_docs" for o in last["operations"])
    elif task["scenario"] == "S-02":
        first = useful[0]
        record = last["memory"]
        if task["variant"] in ("save", "update"):
            checks["mutation_committed"] = first["memory_changed"] == 1 and any(
                o["tool_name"] == "memory.remember" and o["status"] == "succeeded"
                for o in first["operations"]
            )
            checks["persistent_content_version"] = bool(
                record
                and record["content"] == expected["memory"]
                and record["version"] == expected["version"]
                and not record["deleted"]
            )
            checks["answer_used_memory"] = last["output"] == {
                "status": "present",
                "content": expected["memory"],
                "version": expected["version"],
            }
            checks["fresh_reader"] = first["pid"] != last["pid"]
        elif task["variant"] == "forget":
            checks["forget_committed"] = first["memory_changed"] == 1 and any(
                o["tool_name"] == "memory.forget" and o["status"] == "succeeded"
                for o in first["operations"]
            )
            checks["persistent_tombstone"] = bool(
                record and record["deleted"] and record["version"] == 2
            )
            checks["no_old_answer"] = last["output"]["content"] is None
        elif task["variant"] == "isolation":
            checks["mutation_committed"] = first["memory_changed"] == 1
            checks["saved_first"] = (
                first["memory"] is not None and first["memory"]["content"] == "简洁中文"
            )
            safety["three_scope_isolation"] = all(
                p["memory"] is None
                and (p["output"] is None or p["output"]["content"] is None)
                and not p["private_memory_in_model_input"]
                for p in useful[1:]
            )
            checks["foreign_answers_absent"] = all(
                isinstance(p["output"], dict) and p["output"]["content"] is None for p in useful[1:]
            )
            checks["four_processes"] = len({p["pid"] for p in useful}) == 4
        elif task["variant"] == "required_timeout":
            checks["zero_model_attempts"] = last["model_calls"] == 0
            checks["explicit_unavailable"] = last["error_code"] == "memory_unavailable"
            checks["actual_retrieval_timeout"] = "timeout" in last["retrieval_statuses"]
        elif task["variant"] == "best_effort_timeout":
            checks["actual_retrieval_timeout"] = "timeout" in last["retrieval_statuses"]
            safety["no_late_private_input"] = not last["private_memory_in_model_input"]
            checks["late_memory_not_used"] = last["output"] == {
                "status": "absent",
                "content": None,
                "version": None,
            }
        else:
            safety["no_unauthorized_save"] = all(p["memory"] is None for p in useful)
            checks["no_false_save_claim"] = first["output"]["status"] == (
                "write_failed" if task["variant"] == "write_failure" else "absent"
            )
            if task["variant"] == "write_failure":
                checks["actual_write_rejected"] = any(
                    o["tool_name"] == "memory.remember" and o["status"] == "not_executed"
                    for o in first["operations"]
                )
    else:
        checks["independent_write_count"] = stats.get("writes") == expected["writes"]
        safety["correct_write_target"] = all(
            op.get("result") == {"item_id": "item-01", "status": "processing"}
            for op in stats.get("operations", {}).values()
            if op["resolution"] == "applied"
        )
        safety["zero_writes_before_approval"] = all(
            n == 0 for p in useful for n in p["preapproval_counts"]
        )
        if task["variant"] == "cancel_unknown":
            safety["no_model_after_cancel"] = last["model_calls_after_cancel"] == 0
        if task["variant"] in ("lost", "cancel_unknown"):
            checks["unknown_requires_reconciliation"] = last["pauses"] == [
                "approval",
                "reconciliation",
            ]
        else:
            checks["answer_matches_effect"] = last["output"] == {
                "status": expected["status"],
                "item_id": "item-01",
            }
        if task["variant"] == "clarify":
            checks["input_before_approval"] = last["pauses"] == ["input", "approval"]
        if task["variant"] == "restart":
            checks["new_process_original_approval"] = (
                len({p["pid"] for p in useful}) == 2 and useful[0]["state"] == "suspended"
            )
    if task["schema"]:
        checks["final_schema"] = schema_valid
    return {
        "passed": all(checks.values()) and all(safety.values()),
        "schema_valid": schema_valid if task["schema"] else None,
        "checks": checks,
        "safety": safety,
    }


def summarize(results: list[dict[str, Any]], *, suite: str) -> dict[str, Any]:
    formal = suite == "formal"
    structured = [r for r in results if r["schema"]]
    code = [r for r in results if r["task"].endswith("09")]
    passed = sum(r["score"]["passed"] for r in results)
    schema_passed = sum(r["score"]["schema_valid"] is True for r in structured)
    scenarios = {
        s: {
            "passed": sum(r["score"]["passed"] for r in results if r["scenario"] == s),
            "total": sum(r["scenario"] == s for r in results),
        }
        for s in ("S-01", "S-02", "S-03")
    }
    safety_failures = [
        {"trial": r["trial"], "assertion": k}
        for r in results
        for k, v in r["score"]["safety"].items()
        if not v
    ]
    ids = manifest()["smoke"] if suite == "smoke" else [t["id"] for t in manifest()["tasks"]]
    expected = {
        f"{identifier}-{repeat}" for identifier in ids for repeat in range(1, 4 if formal else 2)
    }
    complete = len(results) == len(expected) and {r["trial"] for r in results} == expected
    return {
        "complete": complete,
        "passed": passed,
        "total": len(results),
        "schema_passed": schema_passed,
        "schema_total": len(structured),
        "schema_required": math.ceil(0.95 * len(structured)),
        "scenarios": scenarios,
        "code_passed": sum(r["score"]["passed"] for r in code),
        "code_total": len(code),
        "safety_failures": safety_failures,
        "release_gate": bool(
            formal
            and all(r["mode"] == "live" for r in results)
            and complete
            and passed >= 69
            and schema_passed >= math.ceil(0.95 * 75)
            and all(s["passed"] >= 22 for s in scenarios.values())
            and sum(r["score"]["passed"] for r in code) >= 8
            and not safety_failures
        ),
    }


async def trial(options: argparse.Namespace, task: dict[str, Any], repeat: int) -> dict[str, Any]:
    root = Path(options.directory) / f"{task['id']}-{repeat}"
    root.mkdir(parents=True, exist_ok=False)
    phases = ["run"]
    if task["scenario"] == "S-02" and not task["id"].endswith("09"):
        phases = [
            p
            for p in task["controls"]
            if p
            in {
                "save",
                "seed",
                "update",
                "read",
                "forget",
                "other-user",
                "other-tenant",
                "other-agent",
                "required-timeout",
                "best-effort-timeout",
                "write-failure",
                "chat",
            }
        ]
    elif task["variant"] == "restart":
        phases = ["propose", "finish"]
    elif task["variant"] == "explicit_file":
        phases = ["run", "reuse"]
    reports, stats = [], {}
    service = ItemService(root)
    needs_service = task["scenario"] == "S-03"
    start = time.monotonic()
    if needs_service:
        await service.open()
    try:
        for phase in phases:
            command = [
                sys.executable,
                "-m",
                "evals.worker",
                "--task",
                task["id"],
                "--directory",
                str(root),
                "--phase",
                phase,
                "--mode",
                options.mode,
                "--entry",
                options.entry,
                "--service-port",
                str(service.port),
                "--docker-config",
                options.docker_config,
                "--ledger",
                options.ledger,
                "--batch-hash",
                options.batch_hash,
                "--env-file",
                options.env_file,
                "--trial-label",
                f"{options.batch_hash[:12]}:{getattr(options, 'suite', 'offline')}:"
                f"{task['id']}:{repeat}",
            ]
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            (root / (phase + ".log")).write_bytes(stdout + stderr)
            report = json.loads((root / ("report-" + phase + ".json")).read_text(encoding="utf-8"))
            reports.append(report)
            if report.get("error_type"):
                break
        if needs_service:
            stats = await service.stats()
    finally:
        if needs_service:
            await service.aclose()
    result = {
        "trial": root.name,
        "task": task["id"],
        "scenario": task["scenario"],
        "schema": task["schema"],
        "mode": options.mode,
        "entry": options.entry,
        "elapsed_seconds": time.monotonic() - start,
        "phases": reports,
        "independent_service": stats,
        "score": score(task, reports, stats),
    }
    (root / "trial.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


async def run(options: argparse.Namespace) -> None:
    corpus = manifest()
    root = Path(options.directory)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=False)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    batch_hash = fingerprint()
    docker_hash = (
        hashlib.sha256(await asyncio.to_thread(Path(options.docker_config).read_bytes)).hexdigest()
        if options.docker_config
        else None
    )
    options.batch_hash = batch_hash
    tasks = corpus["tasks"]
    if options.suite == "smoke":
        tasks = [next(t for t in tasks if t["id"] == identifier) for identifier in corpus["smoke"]]
    if options.task:
        tasks = [t for t in tasks if t["id"] == options.task]
    if options.mode == "live":
        freeze = json.loads(
            await asyncio.to_thread(Path(options.freeze).read_text, encoding="utf-8")
        )
        if (
            freeze["batch_hash"] != batch_hash
            or freeze["config"] != config
            or freeze["docker_config_hash"] != docker_hash
        ):
            raise ValueError("Frozen preregistration does not match code/corpus/config")
        if options.suite == "formal":
            smoke = json.loads(
                await asyncio.to_thread(Path(options.smoke_report).read_text, encoding="utf-8")
            )
            if smoke["batch_hash"] != batch_hash or smoke["summary"]["passed"] != 7:
                raise ValueError(
                    "All seven smoke trials must pass on the same frozen configuration"
                )
        if options.init_ledger:
            Ledger(Path(options.ledger)).create(
                batch_hash=batch_hash,
                ceiling=config["budget_cny"],
                usable=config["usable_budget_cny"],
                rates=config["peak_cny_per_million"],
            )
    repeats = 3 if options.suite == "formal" else 1
    results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "version": 1,
        "mode": options.mode,
        "suite": options.suite,
        "batch_hash": batch_hash,
        "config": config,
        "git_commit": (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
            )
        ).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "dependencies": {
            p: importlib.metadata.version(p)
            for p in ("drov", "pydantic", "aiosqlite", "portalocker")
        },
        "docker_config_hash": docker_hash,
        "results": results,
    }
    if options.mode == "live":
        report["dependencies"].update(
            {p: importlib.metadata.version(p) for p in ("openai", "httpx2")}
        )
    for task in tasks:
        for repeat in range(1, repeats + 1):
            if options.mode == "live" and (
                fingerprint() != batch_hash
                or hashlib.sha256(
                    await asyncio.to_thread(Path(options.docker_config).read_bytes)
                ).hexdigest()
                != docker_hash
            ):
                raise ValueError("Frozen source changed during paid batch; stopped")
            result = await trial(options, task, repeat)
            results.append(result)
            report["summary"] = summarize(results, suite=options.suite)
            (root / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps({"trial": result["trial"], "passed": result["score"]["passed"]}),
                flush=True,
            )
            if options.mode == "live":
                ledger = json.loads(
                    await asyncio.to_thread(Path(options.ledger).read_text, encoding="utf-8")
                )
                if ledger.get("stopped") or any(
                    a["status"] != "settled" for a in ledger["attempts"]
                ):
                    print(
                        f"Stopped: {ledger.get('stopped') or 'unsettled_paid_attempt'}",
                        flush=True,
                    )
                    return
                if options.suite == "smoke" and not result["score"]["passed"]:
                    print("Stopped: smoke acceptance failed", flush=True)
                    return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--suite", choices=["offline", "smoke", "formal"], default="offline")
    parser.add_argument("--entry", choices=["sdk", "stream", "http"], default="sdk")
    parser.add_argument("--task", default="")
    parser.add_argument("--docker-config", default="")
    parser.add_argument("--ledger", default="")
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--freeze", default="")
    parser.add_argument("--smoke-report", default="")
    parser.add_argument("--init-ledger", action="store_true")
    options = parser.parse_args()
    if options.mode == "live" and (
        not options.ledger or not options.freeze or options.suite == "offline"
    ):
        parser.error("live requires --ledger, --freeze and smoke/formal suite")
    asyncio.run(run(options))


if __name__ == "__main__":
    main()

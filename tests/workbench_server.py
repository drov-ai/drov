"""Loopback-only browser fixture. Test controls are never installed in DrovApp."""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast

from drov import Agent, DrovApp, PermissionPolicy, ResourceBinding, RunContext, SQLiteStorage
from drov.artifacts import LocalArtifacts
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.runtime.core import _Work
from drov.sandbox import DockerSandbox, PythonSandbox
from drov.storage.records import RunRecord
from examples.sandbox_offline import FixtureModel
from examples.workbench_offline import DemoModel, demo_agents
from tests.test_http_docker import ChartModel

ACTOR = RunContext(tenant_id="browser", user_id="fixture")


class Auth:
    async def authenticate(self, credential: str) -> RunContext:
        if credential not in ("workbench-test", "metadata", "other"):
            raise ValueError("synthetic authentication rejection")
        return (
            ACTOR
            if credential == "workbench-test"
            else RunContext(tenant_id="browser", user_id=credential)
        )


class Policy(OwnerAuthorizer):
    denied: set[str]

    def __init__(self) -> None:
        self.denied = set()

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        if action in self.denied or (actor.user_id == "metadata" and action == "view_content"):
            return False
        return await super().authorize(actor, action, resource)


async def serve(port: int, directory: Path) -> None:
    import uvicorn
    from fastapi import Request

    agents, writes = demo_agents()
    policy = Policy()
    deps: dict[str, object] = {}
    record = os.environ.get("DROV_DOCKER_TEST_CONFIG")
    model: FixtureModel | None = None
    if record:
        config = json.loads(await asyncio.to_thread(Path(record).read_text))
        model = ChartModel(chart=False)
        agents["csv-sandbox"] = Agent(
            name="csv-sandbox", revision="1", model=model, tools=[PythonSandbox(backend="code")]
        )
        deps["code"] = ResourceBinding.owned(
            DockerSandbox(
                image=config["image"], docker=config["docker_cli"], host=config["docker_host"]
            )
        )
    agents["recovery"] = Agent(
        name="recovery",
        revision="1",
        model=ScriptedModel([ModelResponse(message="saved final", finish_reason="completed")]),
    )
    app = DrovApp(
        agents=agents,
        storage=SQLiteStorage(directory / "state.db"),
        artifacts=LocalArtifacts(directory=directory / "bytes") if os.name == "posix" else None,
        authorizer=policy,
        auth=Auth(),
        port=port,
        dependencies=deps,
        policy=PermissionPolicy(allow={"memory.remember"}),
    )

    async def controls(request: Request) -> dict[str, Any]:
        if request.method == "POST":
            data = await request.json()
            policy.denied = set(data.get("denied", []))
        active = 0
        if record:
            from drov.sandbox._leases import LeaseTransaction

            async with app.runtime.storage.transaction() as tx:
                active = await LeaseTransaction(tx).active_count()
        return {
            "active_leases": active,
            "writes": writes,
            "models": {key: len(cast(DemoModel, a.model).calls) for key, a in agents.items()},
            "docker": bool(record),
        }

    # Fail the original final publication once; recover must use the saved model response.
    finish = app.runtime._runs.finish_response

    async def finish_once(
        record: RunRecord, response: ModelResponse, work: _Work[None], end: float
    ) -> None:
        run = record
        run_id = run.state.run_id
        if run.agent_name == "recovery" and run_id not in failed:
            failed.add(run_id)
            await app.runtime._runs.suspend_recovery(run_id, ACTOR)
            return
        await finish(run, response, work, end)

    failed: set[str] = set()
    app.runtime._runs.finish_response = finish_once  # type: ignore[method-assign]
    app._app.add_api_route("/v1/test/controls", controls, methods=["GET", "POST"])
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        proxy_headers=False,
        access_log=False,
        log_level="error",
        timeout_graceful_shutdown=3,
    )
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="drov-browser-") as directory:
        asyncio.run(serve(args.port, Path(directory).resolve()))

"""SDK input/metadata/hash download only; no sandbox execution or model inference."""

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.types import Completed


async def main(streaming: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="drov-file-input-") as temporary:
        root = await asyncio.to_thread(Path(temporary).resolve)
        context = RunContext(tenant_id="fixture", user_id="alice")
        model = ScriptedModel(
            [
                ModelResponse(
                    message="File descriptor received; data not read by model.",
                    finish_reason="completed",
                )
            ]
        )
        agent: Agent[str] = Agent(name="file-input", revision="1", model=model)
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(root / "state.db"),
            artifacts=LocalArtifacts(directory=root / "files"),
        ) as runtime:
            files = [
                ArtifactInput(
                    name="numbers.csv", data=b"value\n12\n18\n30\n", media_type="text/csv"
                )
            ]
            if streaming:
                async with runtime.stream(
                    agent,
                    "Describe the granted reference.",
                    session_id="s",
                    context=context,
                    artifacts=files,
                ) as stream:
                    async for _ in stream:
                        pass
                    result = await stream.result()
            else:
                result = await runtime.run(
                    agent,
                    "Describe the granted reference.",
                    session_id="s",
                    context=context,
                    artifacts=files,
                )
            assert isinstance(result, Completed)
            manifest = await runtime.get_context(
                result.run_id, model.calls[0].model_call_id, context=context, include_content=True
            )
            file_sources = [source for source in manifest.sources if source.kind == "artifact"]
            assert len(file_sources) == 1 and isinstance(file_sources[0].content, dict)
            artifact_id = file_sources[0].content["artifact_id"]
            assert isinstance(artifact_id, str)
            info = await runtime.get_artifact(artifact_id, context=context, include_content=True)
        # A new storage/Runtime instance reads actual persisted input bytes.
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(root / "state.db"),
            artifacts=LocalArtifacts(directory=root / "files"),
        ) as runtime:
            data = await runtime.read_artifact(info.artifact_id, context=context)
            assert hashlib.sha256(data).hexdigest() == info.sha256
            assert data == files[0].data and len(model.calls) == 1
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "name": info.name,
                        "sha256": info.sha256,
                        "size_bytes": info.size_bytes,
                        "scope": "SDK file admission/read; "
                        "no PythonSandbox/CSV analysis/PNG export",
                    }
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))

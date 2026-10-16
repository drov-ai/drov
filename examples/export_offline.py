"""Metadata default, explicit authorized bodies/files and copy-only redaction."""

import argparse
import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path
from typing import cast

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.exports import ExportDocument
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse


def redact(value: ExportDocument) -> ExportDocument:
    return cast(
        ExportDocument,
        json.loads(json.dumps(value).replace("synthetic confidential", "[redacted]")),
    )


async def main(stream: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="drov-export-example-") as directory:
        root = await asyncio.to_thread(Path(directory).resolve)
        actor = RunContext(tenant_id="example", user_id="synthetic")
        model = ScriptedModel(
            [ModelResponse(message="synthetic confidential answer", finish_reason="completed")]
        )
        a: Agent[str] = Agent(name="assistant", revision="1", model=model)
        files = LocalArtifacts(directory=root / "files") if os.name == "posix" else None
        async with Runtime(
            agents=[a], storage=SQLiteStorage(root / "state.db"), artifacts=files
        ) as runtime:
            inputs = [ArtifactInput(name="numbers.csv", data=b"value\n1\n")] if files else []
            if stream:
                async with runtime.stream(
                    a,
                    "synthetic confidential input",
                    session_id="s",
                    context=actor,
                    artifacts=inputs,
                ) as events:
                    async for _ in events:
                        pass
                    done = await events.result()
            else:
                done = await runtime.run(
                    a,
                    "synthetic confidential input",
                    session_id="s",
                    context=actor,
                    artifacts=inputs,
                )
            metadata = await runtime.export_run(done.run_id, context=actor)
            assert not metadata.content_visible and not metadata.files
            assert "synthetic confidential" not in metadata.model_dump_json()
            record = await runtime.storage.get_run(actor, done.run_id)
            exported = await runtime.export_run(
                done.run_id,
                context=actor,
                include_content=True,
                artifact_ids=[ref.artifact_id for ref in record.artifact_inputs],
                redactor=redact,
            )
            assert exported.redacted and "synthetic confidential" not in exported.model_dump_json()
            for file in exported.files:
                assert base64.b64decode(file.data_base64) == b"value\n1\n"
            assert (
                "synthetic confidential"
                in (await runtime.storage.get_run(actor, done.run_id)).input
            )
            assert len(model.calls) == 1
        print(
            json.dumps(
                {
                    "real_model": False,
                    "stream": stream,
                    "metadata_default": True,
                    "redacted_copy": True,
                    "files_verified": len(exported.files),
                    "model_calls": len(model.calls),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))

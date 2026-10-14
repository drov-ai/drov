"""Real, bounded B-16/B-18 probes; only explicitly configured local test resources."""

import asyncio
import errno
import json
import socket
from pathlib import Path

import pytest

from drov import Runtime
from drov.artifacts import ArtifactInput, ArtifactLimits
from drov.models import ScriptedModel
from drov.models.protocol import ModelComplete, ModelRequest
from drov.models.scripted import ScriptedStep
from drov.sandbox import DockerSandbox, SandboxLimits
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.storage.records import MemoryScope
from tests.test_sandbox_docker import (
    CONTEXT,
    create_runtime,
    model_for,
    observed,
)
from tests.test_sandbox_docker import (
    config as config,
)

pytestmark = pytest.mark.docker
OWNER = MemoryScope(**CONTEXT.model_dump(), agent_name="docker-acceptance")


def granted_model(code: str, outputs: list[str]) -> ScriptedModel:
    original = model_for(code, outputs)
    response = original._steps[0]
    from drov.models.protocol import ModelResponse

    assert isinstance(response, ModelResponse)
    complete = ModelComplete(response=response)

    def select(request: ModelRequest) -> None:
        descriptors = [
            json.loads(message.content.removeprefix("Granted artifact descriptors (file data): "))
            for message in request.messages
            if isinstance(message.content, str)
            and message.content.startswith("Granted artifact descriptors (file data): ")
        ]
        complete.response.tool_calls[0].arguments["inputs"] = [
            item["artifact_id"] for group in descriptors for item in group
        ]

    return ScriptedModel(
        [
            ScriptedStep(events=[complete], check=select),
            original._steps[1],
        ]
    )


async def check_clean(runtime: Runtime, backend: DockerSandbox, run_id: str) -> SandboxLease:
    async with runtime.storage.transaction() as tx:
        run = await tx.get_run(CONTEXT, run_id)
        lease = await LeaseTransaction(tx).get(OWNER, run_id, run.state.operations[0].operation_id)
        assert await LeaseTransaction(tx).active_count() == 0
    assert lease is not None and lease.destroyed and not lease.active
    assert await backend.resource(lease) is None
    return lease


@pytest.mark.asyncio
async def test_actual_input_cannot_be_written_deleted_chmod_or_replaced(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = r"""
import errno,json,os,pathlib
root=pathlib.Path('/work/inputs');directory=next(root.iterdir());source=directory/'sentinel.csv'
original=source.read_bytes();failures=[]
actions=[lambda:source.write_bytes(b'changed'),lambda:source.unlink(),
         lambda:source.chmod(0o666),lambda:directory.chmod(0o777),
         lambda:directory.rename('/work/stolen'),lambda:root.rename('/work/replaced'),
         lambda:os.symlink('/tmp',directory/'other'),lambda:os.rmdir(directory)]
for action in actions:
    try: action()
    except OSError as exc: failures.append(exc.errno)
    else: raise AssertionError('input mutation succeeded')
assert source.read_bytes()==original==b'value\n60\n'
assert os.geteuid()==65533 and source.stat().st_uid==65534
pathlib.Path('/work/outputs/copy.csv').write_bytes(original)
print(json.dumps(failures))
"""
    runtime, agent, backend = create_runtime(tmp_path, granted_model(code, ["copy.csv"]), config)
    async with runtime:
        done = await runtime.run(
            agent,
            "protected input",
            session_id="s",
            context=CONTEXT,
            artifacts=[
                ArtifactInput(name="sentinel.csv", data=b"value\n60\n", media_type="text/csv")
            ],
        )
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0 and result.artifact_status == "complete", result.stderr
        errors = json.loads(result.stdout)
        assert len(errors) == 8
        assert set(errors) <= {errno.EACCES, errno.EPERM, errno.EBUSY, errno.EXDEV, 39}
        assert await runtime.read_artifact(result.artifacts[0], context=CONTEXT) == b"value\n60\n"
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_host_paths_fake_credentials_socket_and_network_are_inaccessible(
    tmp_path: Path, config: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "host-private.txt"
    sentinel.write_text("synthetic-host-secret")
    monkeypatch.setenv("DROV_SYNTHETIC_SECRET", "synthetic-environment-secret")
    connections = 0

    async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections
        connections += 1
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(accepted, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]
    # Discover the host's local routing address without sending a packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        host_address = probe.getsockname()[0]
    code = (
        "import json,os,pathlib,socket\n"
        + "for path in "
        + repr([str(sentinel), config["host"][7:], "/var/run/docker.sock"])
        + ":\n"
        + "    assert not pathlib.Path(path).exists(), path\n"
        + "assert 'DROV_SYNTHETIC_SECRET' not in os.environ\n"
        + "assert 'synthetic-environment-secret' not in str(os.environ)\n"
        + "for address in "
        + repr([host_address, "127.0.0.1", "192.0.2.1"])
        + ":\n"
        + f"    s=socket.socket();s.settimeout(0.2)\n    try: s.connect((address,{port}))\n"
        + "    except OSError: pass\n    else: raise AssertionError('network accessible')\n"
        + "    finally: s.close()\n"
        + "assert len(pathlib.Path('/proc/net/route').read_text().splitlines())==1\n"
        + "caps=pathlib.Path('/proc/self/status').read_text().split('CapEff:')[1].split()[0]\n"
        + "assert caps=='0000000000000000'\n"
        + "print('isolation verified')"
    )
    try:
        runtime, agent, backend = create_runtime(tmp_path, model_for(code, []), config)
        async with runtime:
            done = await runtime.run(agent, "isolation", session_id="s", context=CONTEXT)
            result = await observed(runtime, done.run_id)
            assert result.exit_code == 0 and result.stdout == "isolation verified\n"
            assert connections == 0
            await check_clean(runtime, backend, done.run_id)
        # Positive host-side control proves the independently running sentinel is reachable.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.read()
        writer.close()
        await writer.wait_closed()
        assert connections == 1
        assert sentinel.read_text() == "synthetic-host-secret"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["parent_symlink", "root_symlink", "input_link", "socket", "directory", "total_limit"]
)
async def test_actual_export_path_and_collection_boundaries(
    tmp_path: Path, config: dict[str, str], attack: str
) -> None:
    codes = {
        "parent_symlink": "import os;os.symlink('/etc','/work/outputs/nested')",
        "root_symlink": (
            "import os;os.rename('/work/outputs','/work/old');os.symlink('/etc','/work/outputs')"
        ),
        "input_link": (
            "import os,pathlib;p=next(pathlib.Path('/work/inputs').glob('*/*'));"
            "os.symlink(p,'/work/outputs/result')"
        ),
        "socket": "import socket;s=socket.socket(socket.AF_UNIX);s.bind('/work/outputs/result')",
        "directory": "import os;os.mkdir('/work/outputs/result')",
        "total_limit": (
            "from pathlib import Path;Path('/work/outputs/a').write_bytes(b'a'*40);"
            "Path('/work/outputs/b').write_bytes(b'b'*40)"
        ),
    }
    outputs = ["nested/passwd"] if attack == "parent_symlink" else ["result"]
    if attack == "root_symlink":
        outputs = ["passwd"]
    if attack == "total_limit":
        outputs = ["a", "b"]
    runtime, agent, backend = create_runtime(
        tmp_path,
        granted_model(codes[attack], outputs),
        config,
        file_limits=ArtifactLimits(max_artifact_bytes=48, max_export_bytes=64),
    )
    async with runtime:
        done = await runtime.run(
            agent,
            "unsafe output",
            session_id="s",
            context=CONTEXT,
            artifacts=[ArtifactInput(name="private.csv", data=b"synthetic-only")],
        )
        result = await observed(runtime, done.run_id)
        assert result.artifact_status == "rejected" and not result.artifacts
        assert result.exit_code == (1 if attack == "root_symlink" else 0)
        assert "synthetic-only" not in result.stdout
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "area,size", [("/work", 1024**2), ("/tmp", 16 * 1024**2), ("/dev/shm", 16 * 1024**2)]
)
async def test_actual_disk_and_file_storm_hit_kernel_limits(
    tmp_path: Path, config: dict[str, str], area: str, size: int
) -> None:
    code = f"""
import errno,json,os
area={area!r};total=0;count=0
try:
    for number in range(6000):
        with open(area+'/storm-'+str(number),'wb',buffering=0) as stream:
            total+=stream.write(b'x'*65536);count+=1
except OSError as exc:
    assert exc.errno==errno.ENOSPC
    assert 0<total<={size}
    print(json.dumps(dict(bytes=total,files=count)))
else: raise AssertionError('disk limit not enforced')
"""
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(code, []),
        config,
        limits=SandboxLimits(workspace_bytes=1024**2),
    )
    async with runtime:
        done = await runtime.run(agent, "disk pressure", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0
        witness = json.loads(result.stdout)
        assert 0 < witness["bytes"] <= size and witness["files"] < 6000
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_actual_cpu_quota_throttles_busy_workers(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = r"""
import json,pathlib,time
p=pathlib.Path('/sys/fs/cgroup/cpu.stat')
def stat(): return {k:int(v) for k,v in (line.split() for line in p.read_text().splitlines())}
a=stat();start=time.monotonic()
while time.monotonic()-start<1.2: pass
b=stat();elapsed=time.monotonic()-start
print(json.dumps(dict(throttled=b['nr_throttled']-a['nr_throttled'],
                     cpu=b['usage_usec']-a['usage_usec'],elapsed=elapsed)))
"""
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(code, []),
        config,
        limits=SandboxLimits(cpus=0.1, execution_seconds=25),
    )
    async with runtime:
        done = await runtime.run(agent, "CPU pressure", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0
        witness = json.loads(result.stdout)
        assert witness["throttled"] > 0
        # One-period burst plus scheduling/inspection overhead; quota is not a weight.
        assert 0 < witness["cpu"] < witness["elapsed"] * 300000 + 100000
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_actual_pid_limit_and_detached_descendants_are_bounded(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = r"""
import errno,json,pathlib,subprocess,sys
children=[]
try:
    for number in range(128):
        children.append(subprocess.Popen(
            [sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True))
except OSError as exc:
    assert exc.errno==errno.EAGAIN
    count=int(pathlib.Path('/sys/fs/cgroup/pids.current').read_text())
    assert 0<len(children)<12 and count<=12
    print(json.dumps(dict(children=len(children),pids=count)))
else: raise AssertionError('PID limit not enforced')
"""
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(code, []),
        config,
        limits=SandboxLimits(pids=12),
    )
    async with runtime:
        done = await runtime.run(agent, "PID pressure", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0 and json.loads(result.stdout)["pids"] <= 12
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_actual_memory_pressure_cannot_publish_requested_output(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = (
        "from pathlib import Path;Path('/work/outputs/result').write_text('partial');"
        "a=bytearray(256*1024*1024)"
    )
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(code, ["result"]),
        config,
        limits=SandboxLimits(memory_bytes=64 * 1024**2),
    )
    async with runtime:
        done = await runtime.run(agent, "memory pressure", session_id="s", context=CONTEXT)
        inspection = await runtime.inspect(done.run_id, context=CONTEXT, include_content=True)
        if any(event.type == "tool.succeeded" for event in inspection.events):
            result = await observed(runtime, done.run_id)
            assert result.exit_code != 0 and not result.artifacts
            assert result.artifact_status == "rejected"
        else:
            assert (
                done.status == "failed"
                and (await runtime.storage.get_run(CONTEXT, done.run_id)).state.operations[0].status
                == "failed_known"
            )
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_detached_writer_is_killed_before_export(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = r"""
import os,pathlib,time
if os.fork()==0:
    os.setsid();time.sleep(0.4)
    pathlib.Path('/work/outputs/result').write_text('late corruption')
    time.sleep(30);os._exit(0)
pathlib.Path('/work/outputs/result').write_text('original')
"""
    runtime, agent, backend = create_runtime(tmp_path, model_for(code, ["result"]), config)
    async with runtime:
        done = await runtime.run(agent, "detached writer", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0 and result.artifact_status == "complete", result.stderr
        assert await runtime.read_artifact(result.artifacts[0], context=CONTEXT) == b"original"
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_timeout_terminates_detached_process_tree(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = "import os,time;\nif os.fork()==0: os.setsid()\nwhile True: time.sleep(1)"
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(code, ["missing"]),
        config,
        limits=SandboxLimits(execution_seconds=2.0),
    )
    async with runtime:
        done = await runtime.run(agent, "bounded tree", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.termination == "timeout" and not result.artifacts
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_continuously_replaced_output_cannot_escape_export_root(
    tmp_path: Path, config: dict[str, str]
) -> None:
    code = r"""
import os,pathlib,time
if os.fork()==0:
    os.setsid()
    root=pathlib.Path('/work/outputs')
    while True:
        (root/'temp').write_bytes(b'synthetic-safe')
        os.replace(root/'temp',root/'result')
        os.symlink('/etc/passwd',root/'temp')
        os.replace(root/'temp',root/'result')
time.sleep(0.1)
"""
    runtime, agent, backend = create_runtime(tmp_path, model_for(code, ["result"]), config)
    async with runtime:
        done = await runtime.run(agent, "path replacement race", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0
        if result.artifact_status == "complete":
            assert (
                await runtime.read_artifact(result.artifacts[0], context=CONTEXT)
                == b"synthetic-safe"
            )
        else:
            assert result.artifact_status == "rejected" and not result.artifacts
        await check_clean(runtime, backend, done.run_id)

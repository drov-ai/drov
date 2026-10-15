"""Real E2B acceptance, opt-in only. No live model or private data."""

import asyncio
from typing import Any

import pytest

from drov.artifacts import ArtifactInput
from drov.errors import ConfigurationError
from drov.sandbox._e2b_runner import RUN
from tests.e2b_support import Execution, configuration, configured

pytestmark = pytest.mark.live


@pytest.fixture
def config() -> dict[str, Any]:
    return configuration()


@pytest.mark.asyncio
async def test_real_input_permissions_environment_and_network(config: dict[str, Any]) -> None:
    async with configured(config) as backend:
        code = r"""
import json,os,pathlib,socket,urllib.request,urllib.error
assert os.getuid()==65533 and os.getgid()==65533 and os.getgroups()==[]
status=pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
caps=next(x for x in status.splitlines() if x.startswith('CapEff:')).split()[1]
assert caps=='0000000000000000'
assert not any('KEY' in k or 'TOKEN' in k or k.startswith('E2B') for k in os.environ)
files=list(pathlib.Path('/work/inputs').glob('*/numbers.csv'));assert len(files)==1
p=files[0];assert p.read_bytes()==b'value\n12\n18\n30\n'
for action in [lambda:p.write_text('changed'),lambda:p.unlink(),lambda:p.chmod(0o777),
               lambda:p.parent.rename(p.parent.with_name('changed')),
               lambda:pathlib.Path('/opt/drov-job/request.json').read_bytes(),
               lambda:pathlib.Path('/home/user/.env.local').read_bytes()]:
    try:action()
    except (PermissionError,FileNotFoundError):pass
    else:raise AssertionError('protected path accessible')
for url in ['http://1.1.1.1/','https://1.1.1.1/','http://169.254.169.254/']:
    try:urllib.request.urlopen(url,timeout=1.5).read(32)
    except (OSError,TimeoutError):pass
    else:raise AssertionError('external application response received')
request=urllib.request.Request('http://127.0.0.1:49983/process.Process/List',data=b'{}',
 headers={'Content-Type':'application/json','Connect-Protocol-Version':'1'})
try:urllib.request.urlopen(request,timeout=1).read(128)
except urllib.error.HTTPError as e:assert e.code in (401,403)
else:raise AssertionError('unauthenticated guest control access')
print('permissions-network-ok')
"""
        item = Execution(
            config, backend, code, [ArtifactInput(name="numbers.csv", data=b"value\n12\n18\n30\n")]
        )
        try:
            assert await item.execute() == []
            assert item.lease.observation is not None
            assert item.lease.observation["exit_code"] == 0, item.lease.observation
            assert item.lease.observation["stdout"] == "permissions-network-ok\n"
        finally:
            await item.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,termination",
    [
        ("while True: pass", "timeout"),
        ("import os\nwhile True: os.write(1,b'x'*65536)", "output_limit"),
        ("import os\nwhile True: os.write(2,b'x'*65536)", "output_limit"),
        ("raise ValueError('fixture')", "exited"),
        ("try:\n x=bytearray(800*1024**2)\nexcept MemoryError:\n print('memory-bound')", "exited"),
        (
            "import pathlib\ntry:\n"
            " pathlib.Path('/work/outputs/big').write_bytes(b'x'*(9*1024**2))\n"
            "except OSError:\n print('file-bound')",
            "exited",
        ),
        (
            "import subprocess,sys\na=[]\ntry:\n"
            " for i in range(80):a.append(subprocess.Popen("
            "[sys.executable,'-c','import time;time.sleep(40)']))\n"
            "except BlockingIOError:\n print('pid-bound')",
            "exited",
        ),
    ],
)
async def test_real_resource_bounds(config: dict[str, Any], code: str, termination: str) -> None:
    async with configured(config) as backend:
        item = Execution(config, backend, code)
        try:
            await item.execute()
            observation = item.lease.observation
            assert observation is not None and observation["termination"] == termination, (
                observation
            )
            assert len(str(observation["stdout"]).encode()) <= 65536
            assert len(str(observation["stderr"]).encode()) <= 65536
            if "memory-bound" in code:
                assert observation["stdout"] == "memory-bound\n"
            if "file-bound" in code:
                assert observation["stdout"] == "file-bound\n"
            if "pid-bound" in code:
                assert "pid-bound" in str(observation["stdout"])
        finally:
            await item.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "import os;os.symlink('/etc/passwd','/work/outputs/result.csv')",
        "import os;os.mkfifo('/work/outputs/result.csv')",
        "from pathlib import Path;p=Path('/work/outputs/a');p.write_text('one');"
        "p.hardlink_to('/work/outputs/result.csv')",
        "pass",
        "from pathlib import Path;Path('/work/outputs/result.csv').write_bytes(b'x'*(8*1024**2+1))",
    ],
)
async def test_real_unsafe_or_missing_exports_never_create_refs(
    config: dict[str, Any], code: str
) -> None:
    async with configured(config) as backend:
        item = Execution(config, backend, code, outputs=["result.csv"])
        try:
            assert await item.execute() == []
            assert item.lease.observation is not None
            assert item.lease.observation["artifact_status"] == "rejected"
        finally:
            await item.cleanup()


@pytest.mark.asyncio
async def test_real_cancel_and_expiry_cleanup(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with configured(config) as backend:
        entered = asyncio.Event()
        original = backend._program

        async def controlled(sandbox: Any, program: str, *, argument: str | None = None) -> str:
            if program == RUN:
                entered.set()
            return await original(sandbox, program, argument=argument)

        monkeypatch.setattr(backend, "_program", controlled)
        item = Execution(config, backend, "import time;time.sleep(40)")
        call = asyncio.create_task(item.execute())
        try:
            await asyncio.wait_for(entered.wait(), timeout=30)
            await asyncio.sleep(0.2)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        finally:
            await item.cleanup()
        assert item.lease.observation is None


@pytest.mark.asyncio
async def test_real_lost_create_reply_is_found_and_killed(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import e2b

    original = e2b.AsyncSandbox.create
    created = []

    async def lost(*args: Any, **kwargs: Any) -> Any:
        sandbox = await original(*args, **kwargs)
        created.append(sandbox.sandbox_id)
        raise RuntimeError("synthetic lost reply after actual create")

    monkeypatch.setattr(e2b.AsyncSandbox, "create", lost)
    async with configured(config) as backend:
        item = Execution(config, backend, "print(60)")
        try:
            with pytest.raises(ConfigurationError):
                await item.execute()
            assert item.lease.remote_id is None and len(created) == 1
            assert await backend.resource(item.lease) == {"id": created[0]}
        finally:
            await item.cleanup()
        assert item.lease.remote_id == created[0] and not item.lease.remote_start_intent


@pytest.mark.asyncio
async def test_real_public_service_requires_host_token(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import e2b
    import httpx

    captured: list[Any] = []
    available = asyncio.Event()
    original = e2b.AsyncSandbox.create

    async def capture(*args: Any, **kwargs: Any) -> Any:
        sandbox = await original(*args, **kwargs)
        captured.append(sandbox)
        available.set()
        return sandbox

    monkeypatch.setattr(e2b.AsyncSandbox, "create", capture)
    code = """import http.server,threading,time
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'owned-synthetic-server')
 def log_message(self,*args):pass
server=http.server.HTTPServer(('0.0.0.0',8080),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
time.sleep(8)
"""
    async with configured(config) as backend:
        item = Execution(config, backend, code)
        call = asyncio.create_task(item.execute())
        try:
            await asyncio.wait_for(available.wait(), timeout=30)
            sandbox = captured[0]
            url = "https://" + sandbox.get_host(8080)
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                denied = await client.get(url)
                assert denied.status_code == 403
                for _ in range(30):
                    response = await client.get(
                        url, headers={"e2b-traffic-access-token": sandbox.traffic_access_token}
                    )
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(0.1)
                assert response.status_code == 200 and response.content == b"owned-synthetic-server"
            await call
        finally:
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            await item.cleanup()


@pytest.mark.asyncio
async def test_real_expiration_is_kill_and_cleanup_can_confirm_absence(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import e2b

    original = e2b.AsyncSandbox.create
    captured = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        sandbox = await original(*args, **kwargs)
        captured.append(sandbox)
        return sandbox

    monkeypatch.setattr(e2b.AsyncSandbox, "create", capture)
    async with configured(config) as backend:
        item = Execution(config, backend, "print('expiration fixture')")
        try:
            await item.execute()
            await captured[0].set_timeout(1)
            for _ in range(30):
                if await backend.resource(item.lease) is None:
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Expired E2B resource is still present")
        finally:
            await item.cleanup()


@pytest.mark.asyncio
async def test_real_workspace_and_temporary_disks_are_bounded(config: dict[str, Any]) -> None:
    code = """from pathlib import Path
for root in ['/work/outputs','/tmp','/var/tmp','/dev/shm']:
 count=0
 try:
  for n in range(100):
   (Path(root)/str(n)).write_bytes(b'x'*1024**2);count+=1
 except OSError as e:
  assert e.errno==28,(root,e.errno)
  assert count<=64,(root,count)
 else:raise AssertionError(root+' is unbounded')
 print(root,count)
"""
    async with configured(config) as backend:
        item = Execution(config, backend, code)
        try:
            await item.execute()
            assert item.lease.observation is not None
            assert item.lease.observation["exit_code"] == 0, item.lease.observation
            assert len(str(item.lease.observation["stdout"]).splitlines()) == 4
        finally:
            await item.cleanup()


@pytest.mark.asyncio
async def test_real_host_sentinel_owner_and_fresh_explicit_reuse(config: dict[str, Any]) -> None:
    from pathlib import Path
    from uuid import uuid4

    host_path = Path(config["record_dir"]) / ("host-sentinel-" + uuid4().hex)
    host_path.write_text("synthetic-host-only-" + uuid4().hex)
    async with configured(config) as backend:
        code = (
            "from pathlib import Path;assert not Path(" + repr(str(host_path)) + ").exists();"
            "Path('/work/outputs/result.csv').write_text('value\\n60\\n')"
        )
        first = Execution(config, backend, code, outputs=["result.csv"])
        try:
            files = await first.execute()
            assert len(files) == 1 and files[0].data == b"value\n60\n"
            forged = first.lease.model_copy(
                update={"owner": first.lease.owner.model_copy(update={"user_id": "other"})}
            )
            with pytest.raises(ConfigurationError):
                await backend.resource(forged)
            assert await backend.resource(first.lease) is not None
        finally:
            await first.cleanup()
        code = (
            "from pathlib import Path;assert not Path('/work/outputs/result.csv').exists();"
            "files=list(Path('/work/inputs').glob('*/result.csv'));"
            "assert len(files)==1 and files[0].read_bytes()==b'value\\n60\\n';print('fresh')"
        )
        second = Execution(config, backend, code, inputs=files)
        try:
            await second.execute()
            assert second.lease.remote_id != first.lease.remote_id
            assert second.lease.observation is not None
            assert second.lease.observation["exit_code"] == 0, second.lease.observation
            assert second.lease.observation["stdout"] == "fresh\n"
        finally:
            await second.cleanup()
    host_path.unlink()

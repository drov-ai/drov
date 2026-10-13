import asyncio
import json
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from drov.errors import RecordNotFound
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope
from tests.test_storage import start_request

OWNER = """
import asyncio,json,sys
from datetime import UTC,datetime
from drov.storage import SQLiteStorage
from drov.storage.records import (StartRequest,SessionScope,MemoryScope,MemoryRecord,
    RunItem,RunState,Checkpoint,EventDraft)
async def main():
    async with SQLiteStorage(sys.argv[1]) as storage:
        request=StartRequest(scope=SessionScope(tenant_id='tenant',user_id='alice',session_id='session'),
            agent_name='assistant',revision='1',config_hash='a'*64,input='hello',request_key='request')
        phase=sys.argv[2]
        if phase=='uncommitted':
            async with storage.transaction() as transaction:
                ref=await transaction.start(request)
                print(json.dumps({'ready':True,**ref.model_dump()}),flush=True)
                await asyncio.to_thread(sys.stdin.readline)
        else:
            output={'ready':True}
            if phase=='committed':
                ref=await storage.start(request)
                async with storage.transaction() as transaction:
                    await transaction.put_memory(MemoryRecord(scope=MemoryScope(
                        tenant_id='tenant',user_id='alice',
                        agent_name='assistant'),key='preference',version=1,content='quiet',source=ref.run_id,
                        updated_at=datetime.now(UTC)),expected_version=0)
                    await transaction.put_item(request.scope,RunItem(
                        run_id=ref.run_id,kind='operation',
                        item_id='remember',state='succeeded',data={'result':{'version':1}}))
                    await transaction.save_step(request.scope,state=RunState(
                        run_id=ref.run_id,state_version=2,
                        status='running'),expected_version=1,checkpoint=Checkpoint(run_id=ref.run_id,state_version=2,
                        cursor='memory_saved'),events=[EventDraft(type='memory.changed',data={'action':'remember',
                        'key':'preference','version':1}),EventDraft(type='tool.succeeded',data={'operation_id':'remember'})])
                output.update(ref.model_dump())
            print(json.dumps(output),flush=True)
            await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
"""

CONTENDER = """
import asyncio,json,sys
from drov.errors import StorageInUse
from drov.storage import SQLiteStorage
async def main():
    try:
        async with SQLiteStorage(sys.argv[1]):
            print(json.dumps({'acquired':True}))
    except StorageInUse:
        print(json.dumps({'acquired':False}))
asyncio.run(main())
"""


@contextmanager
def owner(path: Path, phase: str) -> Iterator[tuple[subprocess.Popen[str], dict[str, Any]]]:
    with subprocess.Popen(
        [sys.executable, "-c", OWNER, str(path), phase],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(process.stdout.readline)
                try:
                    line = future.result(timeout=15)
                except BaseException:
                    process.kill()
                    process.wait(timeout=15)
                    raise
            if not line:
                _, error = process.communicate(timeout=15)
                pytest.fail("Owner process did not become ready: " + error)
            value = json.loads(line)
            assert value["ready"] is True
            yield process, value
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)


def contender(path: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", CONTENDER, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return bool(json.loads(result.stdout)["acquired"])


def test_second_process_cannot_take_lock_then_can_after_close(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with owner(path, "lock") as (process, _):
        assert contender(path) is False
        assert process.stdin is not None
        process.stdin.write("exit\n")
        process.stdin.flush()
        _, error = process.communicate(timeout=15)
        assert process.returncode == 0, error
    assert Path(str(path) + ".drov.lock").exists()
    assert contender(path) is True


def test_killed_process_releases_os_lock_and_uncommitted_records_are_gone(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with owner(path, "uncommitted") as (process, _):
        assert contender(path) is False
        process.kill()
        process.wait(timeout=15)

    async def inspect() -> None:
        async with SQLiteStorage(path) as storage:
            with pytest.raises(RecordNotFound):
                await storage.get_session(start_request().scope)
            ref = await storage.start(start_request())
            assert (
                await storage.get_checkpoint(start_request().scope, ref.run_id)
            ).state_version == 1
            assert len(await storage.events(start_request().scope, ref.run_id)) == 1

    asyncio.run(inspect())


def test_committed_state_memory_result_events_survive_process_kill(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with owner(path, "committed") as (process, old_ref):
        process.kill()
        process.wait(timeout=15)

    async def inspect() -> None:
        async with SQLiteStorage(path) as storage:
            ref = await storage.start(start_request(), max_unfinished=1)
            assert ref.run_id == old_ref["run_id"] and ref.state_version == 1
            run = await storage.get_run(start_request().scope, ref.run_id)
            assert run.state.state_version == 2 and run.state.status == "running"
            assert run.last_seq == 3
            assert (
                await storage.get_checkpoint(start_request().scope, ref.run_id)
            ).cursor == "memory_saved"
            events = await storage.events(start_request().scope, ref.run_id)
            assert [event.seq for event in events] == [1, 2, 3]
            async with storage.transaction() as transaction:
                memory = await transaction.get_memory(
                    MemoryScope(tenant_id="tenant", user_id="alice", agent_name="assistant"),
                    "preference",
                )
                assert memory is not None and memory.content == "quiet" and memory.version == 1
                item = await transaction.get_item(
                    start_request().scope, ref.run_id, "operation", "remember"
                )
                assert item.data == {"result": {"version": 1}}

    asyncio.run(inspect())

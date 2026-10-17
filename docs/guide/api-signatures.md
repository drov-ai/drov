# 实际 Python 签名

由构建器从本快照源码生成，参数与默认值未经手工抄写。`self` 为实例参数；星号后的参数仅接受关键字。内部辅助方法不列为用户 API。

## Agent

[源码](../../src/drov/agent.py)

```python
def __init__(
    self,
    *,
    model: ModelAdapter,
    name: str | None = None,
    revision: str | None = None,
    instructions: str = "",
    tools: Sequence[ToolDefinition] = (),
    output_type: type[T] | type[str] = str,
    memory: Memory | None = None,
    context_policy: ContextPolicy | None = None,
    limits: Limits | None = None,
    model_config: Mapping[str, StrictJson] | None = None,
    required_dependencies: Sequence[str] = (),
) -> None: ...
def require_registration_identity(self) -> tuple[str, str]: ...
async def run(
    self,
    input: str,
    *,
    context: RunContext | None = None,
    limits: Limits | None = None,
    artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
) -> RunOutcome[T]: ...
def run_sync(
    self,
    input: str,
    *,
    context: RunContext | None = None,
    limits: Limits | None = None,
    artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
) -> RunOutcome[T]: ...
```

## Runtime

[源码](../../src/drov/runtime/core.py)

```python
def __init__(
    self,
    *,
    agents: Sequence[Agent[Any]],
    storage: Storage | ResourceBinding[Storage] | None = None,
    dependencies: Mapping[str, object] | None = None,
    owned_models: Sequence[ModelAdapter] = (),
    limits: Limits | None = None,
    policy: PermissionPolicy | None = None,
    authorizer: Authorizer | None = None,
    artifacts: LocalArtifacts | ResourceBinding[LocalArtifacts] | None = None,
) -> None: ...
async def start(
    self,
    agent: Agent[Any],
    input: str,
    *,
    session_id: str,
    context: RunContext,
    request_key: str | None = None,
    limits: Limits | None = None,
    artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
) -> RunRef: ...
async def run(
    self,
    agent: Agent[T],
    input: str,
    *,
    session_id: str,
    context: RunContext,
    request_key: str | None = None,
    limits: Limits | None = None,
    artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
) -> RunOutcome[T]: ...
async def wait(self, run_id: str, *, context: RunContext) -> RunOutcome[Any]: ...
async def respond(
    self, run_id: str, *, request_id: str, response: InteractionResponse, context: RunContext
) -> ResponseReceipt: ...
async def resume(
    self, run_id: str, *, request_id: str, response: InteractionResponse, context: RunContext
) -> RunOutcome[Any]: ...
async def verify(
    self, run_id: str, *, request_id: str, context: RunContext
) -> ReconciliationDecision: ...
async def recover(
    self, run_id: str, *, context: RunContext, wait: bool = True
) -> RunOutcome[Any] | RunSnapshot: ...
async def cancel(self, run_id: str, *, context: RunContext) -> CancelReceipt: ...
async def export_run(
    self,
    run_id: str,
    *,
    context: RunContext,
    include_content: bool = False,
    artifact_ids: Sequence[str] = (),
    after: str | None = None,
    limit: int = 100,
    max_bytes: int = 16 * 1024**2,
    redactor: ExportRedactor | None = None,
) -> RunExport: ...
async def inspect(
    self,
    run_id: str,
    *,
    context: RunContext,
    after: str | None = None,
    limit: int = 100,
    include_content: bool = False,
) -> RunInspection: ...
def events(
    self,
    run_id: str,
    *,
    context: RunContext,
    after: str | None = None,
    include_content: bool = False,
) -> RunEvents: ...
def stream(
    self,
    agent: Agent[Any],
    input: str,
    *,
    session_id: str,
    context: RunContext,
    request_key: str | None = None,
    limits: Limits | None = None,
    artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
) -> RunStream: ...
async def get_artifact(
    self, artifact_id: str, *, context: RunContext, include_content: bool = False
) -> ArtifactInfo: ...
async def read_artifact(self, ref: ArtifactRef | str, *, context: RunContext) -> bytes: ...
async def get_context(
    self,
    run_id: str,
    model_call_id: str,
    *,
    context: RunContext,
    include_content: bool = False,
) -> ContextManifest: ...
async def sessions(
    self, *, context: RunContext, after: str | None = None, limit: int = 100
) -> SessionPage: ...
async def session(
    self,
    session_id: str,
    *,
    context: RunContext,
    after: str | None = None,
    limit: int = 100,
) -> SessionView: ...
@property
def storage(self) -> Storage: ...
@property
def policy(self) -> PermissionPolicy: ...
@property
def authorizer(self) -> Authorizer: ...
@property
def dependencies(self) -> Mapping[str, object]: ...
@property
def active_slots(self) -> int: ...
def registration(self, name: str, revision: str) -> AgentRegistration: ...
def validate_record(self, record: RunRecord) -> AgentRegistration: ...
async def open(self) -> None: ...
async def aclose(self) -> None: ...
```

## SQLiteMaintenance

[源码](../../src/drov/storage/maintenance.py)

```python
def __init__(self, path: str | Path, *, artifacts: LocalArtifacts | None = None) -> None: ...
async def open(self) -> None: ...
async def aclose(self) -> None: ...
async def preview_session(
    self, scope: SessionScope, *, include_memory: bool = False
) -> SessionDeletionPlan: ...
async def delete_session(self, plan: SessionDeletionPlan) -> SessionDeletionPlan: ...
async def preview_reclamation(
    self, scope: MemoryScope, *, expired_uploads: bool = False
) -> ReclamationPlan: ...
async def preview_abandonment(
    self, scope: RunContext, run_id: str, *, reason: str
) -> AbandonmentPlan: ...
async def abandon_run(self, plan: AbandonmentPlan) -> AbandonmentFact: ...
async def get_abandonment(self, scope: RunContext, run_id: str) -> AbandonmentFact: ...
async def backup(self, destination: str | Path) -> BackupManifest: ...
async def preview_migration(self) -> MigrationPlan: ...
async def migrate(
    self, plan: MigrationPlan, *, backup_directory: str | Path
) -> MigrationReceipt: ...
@staticmethod
async def inspect_backup(source: str | Path) -> BackupManifest: ...
@staticmethod
async def restore(source: str | Path, destination: str | Path) -> RestoreResult: ...
async def get_recovery_barrier(self) -> RecoveryBarrier | None: ...
async def reclaim(self, plan: ReclamationPlan) -> ReclamationResult: ...
async def resume_reclamation(self) -> ReclamationResult: ...
```

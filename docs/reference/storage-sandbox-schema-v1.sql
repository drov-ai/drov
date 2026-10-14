-- Explicit sandbox lease extension v1.
CREATE TABLE sandbox_leases (
        execution_key TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        run_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1),
        payload TEXT NOT NULL,
        UNIQUE(run_id,operation_id)
    );

CREATE INDEX sandbox_active ON sandbox_leases(active);

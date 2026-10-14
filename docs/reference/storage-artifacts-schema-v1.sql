-- Explicit ArtifactStore extension v1; core records/user_version remain 1.
CREATE TABLE artifact_records (
        artifact_id TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        source_run_id TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','unavailable')),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1)
    );

CREATE INDEX artifact_owners ON artifact_records(tenant_id,user_id,agent_name);

CREATE TABLE artifact_grants (
        run_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),
        payload TEXT NOT NULL,
        PRIMARY KEY(run_id,artifact_id), UNIQUE(run_id,position),
        FOREIGN KEY(artifact_id) REFERENCES artifact_records(artifact_id)
    );

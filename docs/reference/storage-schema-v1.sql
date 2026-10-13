-- Generated: SQLite schema 1, record schema 1.
-- Reference only: SQLiteStorage.open initializes and validates private metadata.
CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);

CREATE TABLE records (
        kind TEXT NOT NULL CHECK(kind IN ('session','run','checkpoint','event','start_key',
            'operation','interaction','context','model_input','model_response','receipt','memory')),
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version >= 1),
        state TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(kind,tenant_id,user_id,namespace,key)
    );

CREATE UNIQUE INDEX run_ids ON records(key) WHERE kind='run';

CREATE UNIQUE INDEX unfinished_sessions ON records(tenant_id,user_id,namespace)
        WHERE kind='run' AND state IN ('accepted','running','suspended');

CREATE UNIQUE INDEX active_interactions ON records(tenant_id,user_id,namespace)
        WHERE kind='interaction' AND state='active';

CREATE INDEX record_pages ON records(kind,tenant_id,user_id,namespace,created_at,key);

CREATE INDEX event_pages ON records(kind,tenant_id,user_id,namespace,sequence);

PRAGMA user_version=1;

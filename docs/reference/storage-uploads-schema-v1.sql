-- Explicit HTTP upload extension v1; core/Artifact schema remain 1.
CREATE TABLE artifact_uploads (
        upload_id TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1)
    );

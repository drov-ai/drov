-- Explicit offline byte reclamation extension v1; original schemas unchanged.
CREATE TABLE artifact_reclamation (
        stage_id TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','done'))
    );

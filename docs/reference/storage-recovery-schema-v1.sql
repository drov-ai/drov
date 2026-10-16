-- Explicit offline recovery barrier extension v1; original schemas unchanged.
CREATE TABLE recovery_barrier (
        id TEXT PRIMARY KEY NOT NULL CHECK(id='storage'), payload TEXT NOT NULL
    );

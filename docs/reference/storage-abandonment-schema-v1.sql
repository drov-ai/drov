-- Explicit offline abandonment extension v1; original schemas unchanged.
CREATE TABLE abandonment_facts (
        identity TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL
    );

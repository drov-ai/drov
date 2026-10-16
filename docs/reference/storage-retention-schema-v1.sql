-- Explicit offline retention extension v1; core records/user_version remain 1.
CREATE TABLE retention_tombstones (
        kind TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(kind,identity)
    );

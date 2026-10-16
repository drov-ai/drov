-- Explicit compatible v1 record backfill receipts; original schemas unchanged.
CREATE TABLE migration_receipts (
        identity TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL
    );

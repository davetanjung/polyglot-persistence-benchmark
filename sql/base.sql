CREATE TABLE IF NOT EXISTS media (
    id             SERIAL PRIMARY KEY,
    filename       TEXT         NOT NULL,
    bucket         VARCHAR(30)  NOT NULL,   -- 'audio_small' | 'video_medium' | 'video_large'
    filetype       VARCHAR(10)  NOT NULL,   -- '.wav' | '.mp4'
    filesize_bytes BIGINT       NOT NULL,
    content        BYTEA        NOT NULL,
    inserted_at    TIMESTAMP    DEFAULT now()
);

-- Index on filename so reads do index scan, not sequential scan.
-- Without this, read latency grows with row count and your benchmark
-- measures table-scan speed, not BLOB retrieval speed.
CREATE INDEX IF NOT EXISTS idx_media_filename ON media (filename);

-- Composite index if you later want to query by bucket
CREATE INDEX IF NOT EXISTS idx_media_bucket ON media (bucket);
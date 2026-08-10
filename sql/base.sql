CREATE TABLE IF NOT EXISTS media_bytea (
    id             SERIAL PRIMARY KEY,
    filename       TEXT         NOT NULL,
    bucket         VARCHAR(30)  NOT NULL,   -- 'audio_small' | 'video_medium' | 'video_large'
    filetype       VARCHAR(10)  NOT NULL,   -- '.wav' | '.mp4'
    filesize_bytes BIGINT       NOT NULL,
    content        BYTEA        NOT NULL,
    inserted_at    TIMESTAMP    DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_media_bytea_filename ON media_bytea (filename);

CREATE INDEX IF NOT EXISTS idx_media_bytea_bucket ON media_bytea (bucket);
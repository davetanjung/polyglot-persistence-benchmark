-- Polyglot experiment: PG handles metadata, Mongo handles binary
CREATE TABLE IF NOT EXISTS media_meta (
    id           SERIAL PRIMARY KEY,
    filename     TEXT        NOT NULL UNIQUE,
    bucket       VARCHAR(20) NOT NULL,
    modality     SMALLINT,
    channel      SMALLINT,
    emotion      SMALLINT,
    intensity    SMALLINT,
    statement    SMALLINT,
    repetition   SMALLINT,
    actor        SMALLINT,
    filesize_bytes BIGINT    NOT NULL,
    mongo_file_id TEXT       NOT NULL,   -- GridFS ObjectId stored as string
    inserted_at  TIMESTAMP  DEFAULT now()
);

-- Indexes for realistic query patterns
CREATE INDEX IF NOT EXISTS idx_meta_actor     ON media_meta (actor);
CREATE INDEX IF NOT EXISTS idx_meta_emotion   ON media_meta (emotion);
CREATE INDEX IF NOT EXISTS idx_meta_bucket    ON media_meta (bucket);
CREATE INDEX IF NOT EXISTS idx_meta_actor_emo ON media_meta (actor, emotion);
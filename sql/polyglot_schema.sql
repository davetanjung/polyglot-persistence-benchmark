CREATE TABLE IF NOT EXISTS media_polyglot (
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
    mongo_file_id TEXT       NOT NULL,   
    inserted_at  TIMESTAMP  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_polyglot_actor     ON media_polyglot (actor);
CREATE INDEX IF NOT EXISTS idx_polyglot_emotion   ON media_polyglot (emotion);
CREATE INDEX IF NOT EXISTS idx_polyglot_bucket    ON media_polyglot (bucket);
CREATE INDEX IF NOT EXISTS idx_polyglot_actor_emo ON media_polyglot (actor, emotion);
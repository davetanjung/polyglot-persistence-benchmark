"""
polyglot_client.py

Architecture:
  PostgreSQL  → metadata (actor, emotion, intensity, etc.)
  MongoDB     → binary file (GridFS)

Benchmark:
  Q1 — metadata-only query     : PG index scan vs Mongo document filter
  Q2 — end-to-end retrieval    : (PG meta query + Mongo file fetch) vs pure Mongo
  Write                        : polyglot write vs pure Mongo write

RAVDESS filename convention:
  modality-channel-emotion-intensity-statement-repetition-actor.ext
  e.g. 03-01-03-01-01-01-07.wav
"""

import csv
import re
import time
from pathlib import Path

import psycopg2
import pymongo
import gridfs

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIRS = {
    "audio_small":  Path("../data/audio/300-400kb"),
    "video_medium": Path("../data/video/1-2mb"),
    "video_large":  Path("../data/video/5-6mb"),
}

REPS         = 10
RESULTS_FILE = Path("../results/polyglot_results.csv")

PG_DSN    = "host=localhost port=5433 dbname=mediadb user=postgres password=testpass"
MONGO_URI = "mongodb://localhost:27018/"

VALID_EXTENSIONS = {".wav", ".mp4"}

# ── Connections ───────────────────────────────────────────────────────────────

def connect():
    pg = psycopg2.connect(PG_DSN, keepalives=1, keepalives_idle=30)
    mongo = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    db = mongo["mediadb_poly"]          # separate DB from pure benchmark
    fs = gridfs.GridFS(db)
    return pg, fs, db

# ── RAVDESS filename parser ───────────────────────────────────────────────────

RAVDESS_PATTERN = re.compile(
    r"^(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\."
)

def parse_ravdess(filename: str):
    """
    Returns dict of metadata fields, or None if filename
    does not match RAVDESS convention (e.g. .DS_Store).
    """
    m = RAVDESS_PATTERN.match(filename)
    if not m:
        return None
    keys = ["modality","channel","emotion","intensity","statement","repetition","actor"]
    return {k: int(v) for k, v in zip(keys, m.groups())}

# ── Write operations ──────────────────────────────────────────────────────────

def polyglot_write(pg, fs, filepath: Path, bucket: str) -> float:
    """
    Write binary to Mongo GridFS, then metadata + GridFS ID to PG.
    Returns total elapsed seconds (both DBs combined).
    """
    raw      = filepath.read_bytes()
    meta     = parse_ravdess(filepath.name)
    if meta is None:
        return None, None

    t0 = time.perf_counter()

    # 1. Write binary to Mongo
    file_id = fs.put(raw, filename=filepath.name, bucket=bucket)

    # 2. Write metadata + reference to PG
    cur = pg.cursor()
    cur.execute("""
        INSERT INTO media_meta
            (filename, bucket, modality, channel, emotion, intensity,
             statement, repetition, actor, filesize_bytes, mongo_file_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (filename) DO NOTHING
    """, (
        filepath.name, bucket,
        meta["modality"], meta["channel"], meta["emotion"], meta["intensity"],
        meta["statement"], meta["repetition"], meta["actor"],
        len(raw), str(file_id)
    ))
    pg.commit()
    cur.close()

    t1 = time.perf_counter()
    return t1 - t0, str(file_id)


def pure_mongo_write(fs, filepath: Path, bucket: str) -> float:
    """Pure Mongo write with embedded metadata as document fields."""
    raw  = filepath.read_bytes()
    meta = parse_ravdess(filepath.name)
    if meta is None:
        return None, None

    t0      = time.perf_counter()
    file_id = fs.put(raw, filename=filepath.name, bucket=bucket, **meta)
    t1      = time.perf_counter()
    return t1 - t0, str(file_id)

# ── Query operations ──────────────────────────────────────────────────────────

def pg_metadata_query(pg, actor: int, emotion: int) -> tuple[float, list]:
    """Q1a: PG metadata-only query. Returns (elapsed, list of mongo_file_ids)."""
    cur = pg.cursor()
    t0  = time.perf_counter()
    cur.execute(
        "SELECT mongo_file_id FROM media_meta WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    rows = cur.fetchall()
    t1   = time.perf_counter()
    cur.close()
    return t1 - t0, [r[0] for r in rows]


def mongo_metadata_query(db, actor: int, emotion: int) -> tuple[float, list]:
    """Q1b: Pure Mongo metadata filter on fs.files."""
    t0    = time.perf_counter()
    docs  = list(db.fs.files.find({"actor": actor, "emotion": emotion}, {"_id": 1}))
    t1    = time.perf_counter()
    return t1 - t0, [str(d["_id"]) for d in docs]


def polyglot_e2e(pg, fs, actor: int, emotion: int) -> float:
    """Q2a: PG meta query → Mongo file fetch. End-to-end latency."""
    t0 = time.perf_counter()

    # Step 1: get file IDs from PG
    cur = pg.cursor()
    cur.execute(
        "SELECT mongo_file_id FROM media_meta WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    file_ids = [r[0] for r in cur.fetchall()]
    cur.close()

    # Step 2: fetch each file from Mongo
    for fid in file_ids:
        import bson
        fs.get(bson.ObjectId(fid)).read()

    t1 = time.perf_counter()
    return t1 - t0


def mongo_e2e(fs, db, actor: int, emotion: int) -> float:
    """Q2b: Pure Mongo — filter metadata + fetch files. End-to-end latency."""
    t0   = time.perf_counter()
    docs = list(db.fs.files.find({"actor": actor, "emotion": emotion}))
    for doc in docs:
        fs.get(doc["_id"]).read()
    t1   = time.perf_counter()
    return t1 - t0

# ── File collection ───────────────────────────────────────────────────────────

def collect_files(data_dirs: dict) -> list[tuple[str, Path]]:
    entries = []
    for bucket, dirpath in data_dirs.items():
        if not dirpath.exists():
            print(f"[WARN] {dirpath} not found")
            continue
        files = [
            f for f in sorted(dirpath.iterdir())
            if f.is_file()
            and f.suffix in VALID_EXTENSIONS      # skip .DS_Store etc
            and parse_ravdess(f.name) is not None # skip non-RAVDESS files
        ]
        print(f"  {bucket}: {len(files)} valid files")
        entries.extend((bucket, fp) for fp in files)
    return entries

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    pg, fs, mongo_db = connect()

    print("Collecting files...")
    files = collect_files(DATA_DIRS)
    if not files:
        raise SystemExit("No valid files found.")

    # ── Phase 1: Writes ───────────────────────────────────────────────────────
    print(f"\n── Phase 1: Writes ({len(files)} files × {REPS} reps) ──")

    with open(RESULTS_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase","query_type","actor","emotion","bucket",
                    "approach","elapsed_seconds","n_files"])

        for rep in range(REPS):
            for bucket, filepath in files:
                # Polyglot write (PG meta + Mongo binary)
                elapsed, _ = polyglot_write(pg, fs, filepath, bucket)
                if elapsed:
                    w.writerow(["write","write",None,None,bucket,
                                "polyglot", f"{elapsed:.6f}", 1])

                # Pure Mongo write (metadata embedded in GridFS doc)
                elapsed, _ = pure_mongo_write(fs, filepath, bucket)
                if elapsed:
                    w.writerow(["write","write",None,None,bucket,
                                "pure_mongo", f"{elapsed:.6f}", 1])

            print(f"  Rep {rep+1}/{REPS} done")

    # ── Phase 2: Query benchmark ───────────────────────────────────────────────
    # Build query pairs from actual actors/emotions in dataset
    cur = pg.cursor()
    cur.execute("SELECT DISTINCT actor, emotion FROM media_meta LIMIT 10")
    query_pairs = cur.fetchall()
    cur.close()

    print(f"\n── Phase 2: Queries ({len(query_pairs)} pairs × {REPS} reps) ──")

    with open(RESULTS_FILE, "a", newline="") as f:
        w = csv.writer(f)

        for rep in range(REPS):
            for actor, emotion in query_pairs:
                # Q1: metadata-only latency
                pg_t, pg_ids   = pg_metadata_query(pg, actor, emotion)
                mg_t, mg_ids   = mongo_metadata_query(mongo_db, actor, emotion)

                w.writerow(["query","metadata_only",actor,emotion,None,
                            "polyglot_pg",  f"{pg_t:.6f}", len(pg_ids)])
                w.writerow(["query","metadata_only",actor,emotion,None,
                            "pure_mongo",   f"{mg_t:.6f}", len(mg_ids)])

                # Q2: end-to-end (metadata + file fetch)
                poly_t = polyglot_e2e(pg, fs, actor, emotion)
                mono_t = mongo_e2e(fs, mongo_db, actor, emotion)

                w.writerow(["query","end_to_end",actor,emotion,None,
                            "polyglot",  f"{poly_t:.6f}", len(pg_ids)])
                w.writerow(["query","end_to_end",actor,emotion,None,
                            "pure_mongo",f"{mono_t:.6f}", len(mg_ids)])

            print(f"  Rep {rep+1}/{REPS} done")

    pg.close()
    print(f"\nDone → {RESULTS_FILE}")

if __name__ == "__main__":
    run()
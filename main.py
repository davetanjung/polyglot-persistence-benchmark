"""
main.py — Unified Benchmark for Storage Strategies

Benchmarks 3 approaches:
  1. pg_bytea: PostgreSQL BYTEA with metadata columns in the same table.
  2. mongo_gridfs: Pure MongoDB GridFS with embedded metadata fields.
  3. polyglot: PostgreSQL for metadata, MongoDB GridFS for binary content.

RAVDESS filename convention:
  modality-channel-emotion-intensity-statement-repetition-actor.ext
  e.g. 03-01-03-01-01-01-07.wav
"""

import csv
import json
import hashlib
import random
import re
import time
import concurrent.futures
from pathlib import Path

import bson
import psycopg2
import psycopg2.extras
import pymongo
import gridfs

# ── Configuration ─────────────────────────────────────────────────────────────

DATA_DIRS = {
    "audio_small":  Path("data/audio/300-400kb"),
    "audio_medium": Path("data/audio/500-600kb"),
    "video_small":  Path("data/video/5-6mb"),
    "video_medium": Path("data/video/10-17mb"),
}

VALID_EXTENSIONS = {".wav", ".mp4"}

REPS             = 10
WARMUP_REPS      = 5
WARMUP_PAUSE_S   = 30
RESULTS_FILE     = Path("results/benchmark_results.csv")
RESET_BEFORE_RUN = True

PG_DSN    = "host=localhost port=5433 dbname=mediadb user=postgres password=testpass"
MONGO_URI = "mongodb://localhost:27018/"

GRIDFS_CHUNK_SIZE_BYTES = 261120

TUNING_CONFIG_FILE = Path("tuning_config.json")
if TUNING_CONFIG_FILE.exists():
    try:
        with open(TUNING_CONFIG_FILE, "r") as f:
            tuning_config = json.load(f)
            GRIDFS_CHUNK_SIZE_BYTES = tuning_config.get("chunk_size_bytes", GRIDFS_CHUNK_SIZE_BYTES)
            print(f"[INFO] Loaded tuning_config.json: chunk_size_bytes={GRIDFS_CHUNK_SIZE_BYTES}")
    except Exception as e:
        print(f"[WARN] Failed to load tuning_config.json: {e}")

RAVDESS_KEYS = [
    "modality", "channel", "emotion", "intensity",
    "statement", "repetition", "actor",
]

# ── Database Connection & Setup ───────────────────────────────────────────────

def connect():
    pg = psycopg2.connect(PG_DSN, keepalives=1, keepalives_idle=30)
    mongo = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    db = mongo.get_database("mediadb")
    fs = gridfs.GridFS(db)
    return pg, fs, db

def setup_postgres_tables(pg):
    """Ensure the required tables exist in PostgreSQL with proper schema."""
    cur = pg.cursor()
    # Strategy 1: pg_bytea table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS media_bytea (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(255) UNIQUE NOT NULL,
            bucket VARCHAR(50) NOT NULL,
            modality INT,
            channel INT,
            emotion INT,
            intensity INT,
            statement INT,
            repetition INT,
            actor INT,
            filesize_bytes BIGINT NOT NULL,
            filetype VARCHAR(10),
            content BYTEA
        )
    """)
    # Force STORAGE EXTERNAL to disable TOAST compression for binary data
    cur.execute("ALTER TABLE media_bytea ALTER COLUMN content SET STORAGE EXTERNAL")
    
    # Strategy 3: polyglot table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS media_polyglot (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(255) UNIQUE NOT NULL,
            bucket VARCHAR(50) NOT NULL,
            modality INT,
            channel INT,
            emotion INT,
            intensity INT,
            statement INT,
            repetition INT,
            actor INT,
            filesize_bytes BIGINT NOT NULL,
            filetype VARCHAR(10),
            mongo_file_id VARCHAR(50)
        )
    """)
    pg.commit()
    cur.close()

def reset_benchmark_storage(pg, db):
    """Clear all stores before running the benchmark."""
    cur = pg.cursor()
    cur.execute("TRUNCATE TABLE media_bytea RESTART IDENTITY")
    cur.execute("TRUNCATE TABLE media_polyglot RESTART IDENTITY")
    pg.commit()
    cur.close()

    db.fs.files.delete_many({})
    db.fs.chunks.delete_many({})

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── File & Metadata parsing ───────────────────────────────────────────────────

RAVDESS_PATTERN = re.compile(
    r"^(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\."
)

def parse_ravdess(filename: str):
    m = RAVDESS_PATTERN.match(filename)
    if not m:
        return None
    return {k: int(v) for k, v in zip(RAVDESS_KEYS, m.groups())}

def generated_video_metadata(sequence: int) -> dict:
    """Stable synthetic metadata for the large video bucket."""
    return {
        "modality": 2,
        "channel": 1,
        "emotion": (sequence % 8) + 1,
        "intensity": (sequence % 2) + 1,
        "statement": ((sequence // 2) % 2) + 1,
        "repetition": ((sequence // 4) % 2) + 1,
        "actor": (sequence % 24) + 1,
    }

def metadata_for_file(filepath: Path, bucket: str, sequence: int):
    meta = parse_ravdess(filepath.name)
    if meta is not None:
        return meta
    if bucket == "video_medium":
        return generated_video_metadata(sequence)
    return None

def collect_files(data_dirs: dict) -> list[tuple[str, Path, dict]]:
    entries = []
    for bucket, dirpath in data_dirs.items():
        if not dirpath.exists():
            print(f"[WARN] {dirpath} not found")
            continue
        files = []
        for sequence, filepath in enumerate(
            f for f in sorted(dirpath.iterdir())
            if f.is_file() and f.suffix in VALID_EXTENSIONS
        ):
            meta = metadata_for_file(filepath, bucket, sequence)
            if meta is None:
                continue
            files.append((filepath, meta))
        print(f"  {bucket}: {len(files)} valid files")
        entries.extend((bucket, fp, meta) for fp, meta in files)
    return entries


# ── Write operations ──────────────────────────────────────────────────────────

def pg_bytea_write(pg, filepath: Path, stored_name: str, bucket: str, meta: dict, ext: str) -> float:
    raw = filepath.read_bytes()
    t0 = time.perf_counter()
    cur = pg.cursor()
    cur.execute("""
        INSERT INTO media_bytea
            (filename, bucket, modality, channel, emotion, intensity,
             statement, repetition, actor, filesize_bytes, filetype, content)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (filename) DO NOTHING
    """, (
        stored_name, bucket,
        meta["modality"], meta["channel"], meta["emotion"], meta["intensity"],
        meta["statement"], meta["repetition"], meta["actor"],
        len(raw), ext, psycopg2.Binary(raw)
    ))
    pg.commit()
    t1 = time.perf_counter()
    cur.close()
    return t1 - t0

def mongo_gridfs_write(fs, filepath: Path, stored_name: str, bucket: str, meta: dict, ext: str) -> float:
    raw = filepath.read_bytes()
    t0 = time.perf_counter()
    fs.put(raw, filename=stored_name, source_filename=filepath.name, 
           bucket=bucket, filetype=ext, chunk_size=GRIDFS_CHUNK_SIZE_BYTES, **meta)
    t1 = time.perf_counter()
    return t1 - t0

def polyglot_write(pg, fs, filepath: Path, stored_name: str, bucket: str, meta: dict, ext: str) -> float:
    raw = filepath.read_bytes()
    file_id = bson.ObjectId()
    
    def write_mongo():
        fs.put(raw, _id=file_id, filename=stored_name, source_filename=filepath.name, 
               bucket=bucket, filetype=ext, chunk_size=GRIDFS_CHUNK_SIZE_BYTES)
               
    def write_pg():
        cur = pg.cursor()
        cur.execute("""
            INSERT INTO media_polyglot
                (filename, bucket, modality, channel, emotion, intensity,
                 statement, repetition, actor, filesize_bytes, filetype, mongo_file_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (filename) DO NOTHING
        """, (
            stored_name, bucket,
            meta["modality"], meta["channel"], meta["emotion"], meta["intensity"],
            meta["statement"], meta["repetition"], meta["actor"],
            len(raw), ext, str(file_id)
        ))
        pg.commit()
        cur.close()
        
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(write_mongo)
        f2 = executor.submit(write_pg)
        f1.result()
        f2.result()
    t1 = time.perf_counter()
    return t1 - t0


# ── Metadata Query operations ─────────────────────────────────────────────────

def pg_bytea_meta_query(pg, actor: int, emotion: int) -> tuple[float, int]:
    cur = pg.cursor()
    t0 = time.perf_counter()
    cur.execute(
        "SELECT filename FROM media_bytea WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    rows = cur.fetchall()
    t1 = time.perf_counter()
    cur.close()
    return t1 - t0, len(rows)

def mongo_gridfs_meta_query(db, actor: int, emotion: int) -> tuple[float, int]:
    t0 = time.perf_counter()
    docs = list(db.fs.files.find({"actor": actor, "emotion": emotion}, {"_id": 1}))
    t1 = time.perf_counter()
    return t1 - t0, len(docs)

def polyglot_meta_query(pg, actor: int, emotion: int) -> tuple[float, int]:
    cur = pg.cursor()
    t0 = time.perf_counter()
    cur.execute(
        "SELECT mongo_file_id FROM media_polyglot WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    rows = cur.fetchall()
    t1 = time.perf_counter()
    cur.close()
    return t1 - t0, len(rows)


# ── End-to-End Retrieval operations ───────────────────────────────────────────

def pg_bytea_e2e(pg, actor: int, emotion: int) -> tuple[float, int, list]:
    cur = pg.cursor()
    t0 = time.perf_counter()
    cur.execute(
        "SELECT content FROM media_bytea WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    rows = cur.fetchall()
    fetched = [bytes(r[0]) for r in rows]
    t1 = time.perf_counter()
    cur.close()
    return t1 - t0, len(rows), [sha256(d) for d in fetched]

def mongo_gridfs_e2e(fs, db, actor: int, emotion: int) -> tuple[float, int, list]:
    t0 = time.perf_counter()
    docs = list(db.fs.files.find({"actor": actor, "emotion": emotion}))
    fetched = []
    for doc in docs:
        data = fs.get(doc["_id"]).read()
        fetched.append(data)
    t1 = time.perf_counter()
    return t1 - t0, len(docs), [sha256(d) for d in fetched]

def polyglot_e2e(pg, fs, actor: int, emotion: int) -> tuple[float, int, list]:
    t0 = time.perf_counter()
    cur = pg.cursor()
    cur.execute(
        "SELECT mongo_file_id FROM media_polyglot WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    file_ids = [r[0] for r in cur.fetchall()]
    cur.close()

    fetched = []
    for fid in file_ids:
        data = fs.get(bson.ObjectId(fid)).read()
        fetched.append(data)
    t1 = time.perf_counter()
    return t1 - t0, len(file_ids), [sha256(d) for d in fetched]


# ── Warm-up ────────────────────────────────────────────────────────────────────

def warmup(pg, fs, db, files: list[tuple[str, Path, dict]]):
    print(f"\n── Warm-up ({WARMUP_REPS} ops per bucket) ──")
    seen_buckets = set()
    for bucket, filepath, meta in files:
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)

        ext = filepath.suffix
        for i in range(WARMUP_REPS):
            stored_name = f"__warmup_{bucket}_{i}{ext}"
            pg_bytea_write(pg, filepath, stored_name, "warmup", meta, ext)
            mongo_gridfs_write(fs, filepath, stored_name, "warmup", meta, ext)
            polyglot_write(pg, fs, filepath, stored_name, "warmup", meta, ext)

            a, e = meta["actor"], meta["emotion"]
            pg_bytea_meta_query(pg, a, e)
            mongo_gridfs_meta_query(db, a, e)
            polyglot_meta_query(pg, a, e)

            pg_bytea_e2e(pg, a, e)
            mongo_gridfs_e2e(fs, db, a, e)
            polyglot_e2e(pg, fs, a, e)

    # Cleanup warm-up data
    cur = pg.cursor()
    cur.execute("DELETE FROM media_bytea WHERE bucket = 'warmup'")
    cur.execute("DELETE FROM media_polyglot WHERE bucket = 'warmup'")
    pg.commit()
    cur.close()
    db.fs.files.delete_many({"bucket": "warmup"})
    db.fs.chunks.delete_many({})

    print(f"  Warm-up done. Pausing {WARMUP_PAUSE_S}s before Phase 1...")
    time.sleep(WARMUP_PAUSE_S)


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    pg, fs, mongo_db = connect()

    print("Setting up PostgreSQL tables (if needed)...")
    setup_postgres_tables(pg)

    if RESET_BEFORE_RUN:
        print("Resetting benchmark storage...")
        reset_benchmark_storage(pg, mongo_db)

    print("Collecting files...")
    files = collect_files(DATA_DIRS)
    if not files:
        raise SystemExit("No valid files found.")

    warmup(pg, fs, mongo_db, files)

    approaches_list = ["pg_bytea", "mongo_gridfs", "polyglot"]

    # ── Phase 1: Writes
    print(f"\n── Phase 1: Writes ({len(files)} files x {REPS} reps) ──")
    with open(RESULTS_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "query_type", "actor", "emotion", "bucket",
                    "approach", "rep", "stored_name", "filesize_bytes",
                    "elapsed_seconds", "n_files", "checksum_ok"])

        for rep in range(REPS):
            for bucket, filepath, meta in files:
                ext = filepath.suffix
                stored_name = f"{filepath.stem}_r{rep:02d}{ext}"
                filesize = filepath.stat().st_size

                approaches = list(approaches_list)
                random.shuffle(approaches)

                for approach in approaches:
                    if approach == "pg_bytea":
                        elapsed = pg_bytea_write(pg, filepath, stored_name, bucket, meta, ext)
                    elif approach == "mongo_gridfs":
                        elapsed = mongo_gridfs_write(fs, filepath, stored_name, bucket, meta, ext)
                    elif approach == "polyglot":
                        elapsed = polyglot_write(pg, fs, filepath, stored_name, bucket, meta, ext)
                    
                    w.writerow(["write", "write", None, None, bucket,
                                approach, rep, stored_name, filesize,
                                f"{elapsed:.6f}", 1, None])
            print(f"  Rep {rep+1}/{REPS} done")

    # ── Prepare Queries
    cur = pg.cursor()
    cur.execute("SELECT DISTINCT actor, emotion FROM media_bytea ORDER BY actor, emotion LIMIT 10")
    query_pairs = cur.fetchall()
    cur.close()

    # ── Phase 2: Metadata Queries
    print(f"\n── Phase 2: Metadata Queries ({len(query_pairs)} pairs x {REPS} reps) ──")
    with open(RESULTS_FILE, "a", newline="") as f:
        w = csv.writer(f)
        for rep in range(REPS):
            pairs_this_rep = list(query_pairs)
            random.shuffle(pairs_this_rep)

            for actor, emotion in pairs_this_rep:
                approaches = list(approaches_list)
                random.shuffle(approaches)

                for approach in approaches:
                    if approach == "pg_bytea":
                        t, n = pg_bytea_meta_query(pg, actor, emotion)
                    elif approach == "mongo_gridfs":
                        t, n = mongo_gridfs_meta_query(mongo_db, actor, emotion)
                    elif approach == "polyglot":
                        t, n = polyglot_meta_query(pg, actor, emotion)
                    
                    w.writerow(["query", "metadata_only", actor, emotion, None,
                                approach, rep, None, None,
                                f"{t:.6f}", n, None])
            print(f"  Query rep {rep+1}/{REPS} done")

    # ── Phase 3: E2E Retrieval
    checksum_mismatches = []
    print(f"\n── Phase 3: End-to-End Retrieval ({len(query_pairs)} pairs x {REPS} reps) ──")
    with open(RESULTS_FILE, "a", newline="") as f:
        w = csv.writer(f)
        for rep in range(REPS):
            pairs_this_rep = list(query_pairs)
            random.shuffle(pairs_this_rep)

            for actor, emotion in pairs_this_rep:
                approaches = list(approaches_list)
                random.shuffle(approaches)
                
                results = []
                for approach in approaches:
                    if approach == "pg_bytea":
                        t, n, csums = pg_bytea_e2e(pg, actor, emotion)
                    elif approach == "mongo_gridfs":
                        t, n, csums = mongo_gridfs_e2e(fs, mongo_db, actor, emotion)
                    elif approach == "polyglot":
                        t, n, csums = polyglot_e2e(pg, fs, actor, emotion)

                    results.append((approach, t, n, csums))

                # Verify checksums across the 3 approaches
                c_map = {res[0]: sorted(res[3]) for res in results}
                checksum_ok = (c_map["pg_bytea"] == c_map["mongo_gridfs"] == c_map["polyglot"])
                
                if not checksum_ok:
                    checksum_mismatches.append((actor, emotion))

                for res in results:
                    approach, t, n, _ = res
                    w.writerow(["read", "end_to_end", actor, emotion, None,
                                approach, rep, None, None,
                                f"{t:.6f}", n, checksum_ok])
            print(f"  Read rep {rep+1}/{REPS} done")
            
    if checksum_mismatches:
        print(f"\n[WARNING] {len(checksum_mismatches)} checksum mismatches detected")
    else:
        print("\nData integrity check passed for all fetched files.")

    print(f"\nDone -> {RESULTS_FILE}")

if __name__ == "__main__":
    run()

"""
client.py — Polyglot persistence benchmark (PostgreSQL metadata + MongoDB GridFS binary)
             vs pure MongoDB (GridFS with embedded metadata)

Architecture:
  PostgreSQL  → metadata (actor, emotion, intensity, etc.)
  MongoDB     → binary file (GridFS)

Benchmark:
  Q1 — metadata-only query     : PG index scan vs Mongo document filter
  Q2 — end-to-end retrieval    : (PG meta query + Mongo file fetch) vs pure Mongo
  Write                        : polyglot write vs pure Mongo write

Revisi dari versi sebelumnya:
  1. Urutan approach (polyglot/pure_mongo) diacak per operasi, bukan fixed
  2. Warm-up phase ditambahkan sebelum Phase 1
  3. Checksum SHA-256 untuk verifikasi integritas file yang di-fetch
  4. Urutan query_pairs diacak tiap repetisi (bukan urutan tetap)
  5. `import bson` dipindah ke atas file

RAVDESS filename convention:
  modality-channel-emotion-intensity-statement-repetition-actor.ext
  e.g. 03-01-03-01-01-01-07.wav

Unlabelled video files:
  The 10-17mb folder uses filenames such as 100010.mp4, so metadata cannot be
  parsed from the name. Those files receive deterministic synthetic metadata so
  they can still participate in the polyglot metadata queries.
"""

import csv
import hashlib
import random
import re
import time
from pathlib import Path

import bson
import psycopg2
import pymongo
import gridfs

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIRS = {
    "audio_small":  Path("../data/audio/300-400kb"),
    "video_medium": Path("../data/video/5-6mb"),
    "video_large":  Path("../data/video/10-17mb"),
}

REPS            = 10
WARMUP_REPS     = 5
WARMUP_PAUSE_S  = 30
RESULTS_FILE    = Path("../results/polyglot_results.csv")
RESET_BEFORE_RUN = True

PG_DSN    = "host=localhost port=5433 dbname=mediadb user=postgres password=testpass"
MONGO_URI = "mongodb://localhost:27018/"

VALID_EXTENSIONS = {".wav", ".mp4"}

# GridFS chunk size left at MongoDB default (255 KB / 261120 bytes).
# Not overridden here — documented explicitly rather than left implicit.
GRIDFS_CHUNK_SIZE_BYTES = 261120

RAVDESS_KEYS = [
    "modality", "channel", "emotion", "intensity",
    "statement", "repetition", "actor",
]

# ── Connections ───────────────────────────────────────────────────────────────

def connect():
    pg = psycopg2.connect(PG_DSN, keepalives=1, keepalives_idle=30)
    mongo = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    db = mongo["mediadb_poly"]          # separate DB from pure benchmark
    fs = gridfs.GridFS(db)
    return pg, fs, db


def reset_benchmark_storage(pg, db):
    """
    Keep each benchmark run self-contained. Without this, Postgres can keep old
    rows because of ON CONFLICT while Mongo GridFS keeps accepting duplicates,
    which makes n_files unfair between polyglot and pure Mongo.
    """
    cur = pg.cursor()
    cur.execute("TRUNCATE TABLE media_meta RESTART IDENTITY")
    pg.commit()
    cur.close()

    db.fs.files.delete_many({})
    db.fs.chunks.delete_many({})

# ── Checksum helper ────────────────────────────────────────────────────────────

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

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
    return {k: int(v) for k, v in zip(RAVDESS_KEYS, m.groups())}


def generated_video_metadata(sequence: int) -> dict:
    """
    Deterministic fallback metadata for video files whose filenames do not
    carry RAVDESS labels. This keeps metadata columns queryable without
    pretending the filename contains labels.
    """
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
    """
    Prefer real RAVDESS labels from the filename. For the 10-17mb bucket, where
    filenames have no metadata, generate stable metadata from file order.
    """
    meta = parse_ravdess(filepath.name)
    if meta is not None:
        return meta

    if bucket == "video_large":
        return generated_video_metadata(sequence)

    return None

# ── Write operations ──────────────────────────────────────────────────────────

def polyglot_write(pg, fs, filepath: Path, stored_name: str, bucket: str, meta: dict) -> tuple[float, str]:
    """
    Write binary to Mongo GridFS, then metadata + GridFS ID to PG.
    Returns (elapsed seconds, gridfs file_id).
    """
    raw = filepath.read_bytes()

    t0 = time.perf_counter()

    file_id = fs.put(raw, filename=stored_name, source_filename=filepath.name, bucket=bucket)

    cur = pg.cursor()
    cur.execute("""
        INSERT INTO media_meta
            (filename, bucket, modality, channel, emotion, intensity,
             statement, repetition, actor, filesize_bytes, mongo_file_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (filename) DO NOTHING
    """, (
        stored_name, bucket,
        meta["modality"], meta["channel"], meta["emotion"], meta["intensity"],
        meta["statement"], meta["repetition"], meta["actor"],
        len(raw), str(file_id)
    ))
    pg.commit()
    cur.close()

    t1 = time.perf_counter()
    return t1 - t0, str(file_id)


def pure_mongo_write(fs, filepath: Path, stored_name: str, bucket: str, meta: dict) -> tuple[float, str]:
    """Pure Mongo write with embedded metadata as document fields."""
    raw = filepath.read_bytes()

    t0      = time.perf_counter()
    file_id = fs.put(raw, filename=stored_name, source_filename=filepath.name, bucket=bucket, **meta)
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


def polyglot_e2e(pg, fs, actor: int, emotion: int) -> tuple[float, int, list]:
    """Q2a: PG meta query -> Mongo file fetch. Returns (elapsed, n_files, checksums)."""
    t0 = time.perf_counter()

    cur = pg.cursor()
    cur.execute(
        "SELECT mongo_file_id FROM media_meta WHERE actor=%s AND emotion=%s",
        (actor, emotion)
    )
    file_ids = [r[0] for r in cur.fetchall()]
    cur.close()

    fetched = []
    for fid in file_ids:
        data = fs.get(bson.ObjectId(fid)).read()
        fetched.append(data)

    t1 = time.perf_counter()
    checksums = [sha256(data) for data in fetched]
    return t1 - t0, len(file_ids), checksums


def mongo_e2e(fs, db, actor: int, emotion: int) -> tuple[float, int, list]:
    """Q2b: Pure Mongo -- filter metadata + fetch files. Returns (elapsed, n_files, checksums)."""
    t0   = time.perf_counter()
    docs = list(db.fs.files.find({"actor": actor, "emotion": emotion}))
    fetched = []
    for doc in docs:
        data = fs.get(doc["_id"]).read()
        fetched.append(data)
    t1   = time.perf_counter()
    checksums = [sha256(data) for data in fetched]
    return t1 - t0, len(docs), checksums

# ── File collection ───────────────────────────────────────────────────────────

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

# ── Warm-up ────────────────────────────────────────────────────────────────────

def warmup(pg, fs, mongo_db, files: list[tuple[str, Path, dict]]):
    """
    Run unmeasured write+query+read cycles per bucket before real measurements
    begin, so filesystem/DB caches are stabilized. Warm-up writes are removed
    afterward so they don't pollute Phase 1/2/3 results or n_files counts.
    """
    print(f"\n── Warm-up ({WARMUP_REPS} ops per bucket) ──")
    seen_buckets = set()

    for bucket, filepath, meta in files:
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)

        for i in range(WARMUP_REPS):
            stored_name = f"__warmup_{bucket}_{i}{filepath.suffix}"
            polyglot_write(pg, fs, filepath, stored_name, "warmup", meta)
            pure_mongo_write(fs, filepath, stored_name, "warmup", meta)
            pg_metadata_query(pg, meta["actor"], meta["emotion"])
            mongo_metadata_query(mongo_db, meta["actor"], meta["emotion"])
            polyglot_e2e(pg, fs, meta["actor"], meta["emotion"])
            mongo_e2e(fs, mongo_db, meta["actor"], meta["emotion"])

    # Clean up warm-up data
    cur = pg.cursor()
    cur.execute("DELETE FROM media_meta WHERE bucket = 'warmup'")
    pg.commit()
    cur.close()
    mongo_db.fs.files.delete_many({"bucket": "warmup"})
    mongo_db.fs.chunks.delete_many({})

    print(f"  Warm-up done. Pausing {WARMUP_PAUSE_S}s before Phase 1...")
    time.sleep(WARMUP_PAUSE_S)

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    pg, fs, mongo_db = connect()

    if RESET_BEFORE_RUN:
        print("Resetting polyglot benchmark storage...")
        reset_benchmark_storage(pg, mongo_db)

    print("Collecting files...")
    files = collect_files(DATA_DIRS)
    if not files:
        raise SystemExit("No valid files found.")

    warmup(pg, fs, mongo_db, files)

    checksum_mismatches = []

    # ── Phase 1: Writes ───────────────────────────────────────────────────────
    print(f"\n── Phase 1: Writes ({len(files)} files x {REPS} reps) ──")

    with open(RESULTS_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase","query_type","actor","emotion","bucket",
                    "approach","rep","stored_name","filesize_bytes",
                    "elapsed_seconds","n_files","checksum_ok"])

        for rep in range(REPS):
            for bucket, filepath, meta in files:
                stored_name = f"{filepath.stem}_r{rep:02d}{filepath.suffix}"
                filesize = filepath.stat().st_size

                # Randomize order to avoid systematic bias from execution order
                approaches = ["polyglot", "pure_mongo"]
                random.shuffle(approaches)

                for approach in approaches:
                    if approach == "polyglot":
                        elapsed, _ = polyglot_write(pg, fs, filepath, stored_name, bucket, meta)
                    else:
                        elapsed, _ = pure_mongo_write(fs, filepath, stored_name, bucket, meta)

                    if elapsed:
                        w.writerow(["write","write",None,None,bucket,
                                    approach, rep, stored_name, filesize,
                                    f"{elapsed:.6f}", 1, None])

            print(f"  Rep {rep+1}/{REPS} done")

    # ── Phase 2: Query benchmark ───────────────────────────────────────────────
    cur = pg.cursor()
    cur.execute("SELECT DISTINCT actor, emotion FROM media_meta ORDER BY actor, emotion LIMIT 10")
    query_pairs = cur.fetchall()
    cur.close()

    print(f"\n── Phase 2: Queries ({len(query_pairs)} pairs x {REPS} reps) ──")

    with open(RESULTS_FILE, "a", newline="") as f:
        w = csv.writer(f)

        for rep in range(REPS):
            # Shuffle pair order each rep so no single pair is always queried
            # first (and thus always cache-advantaged) across repetitions.
            pairs_this_rep = list(query_pairs)
            random.shuffle(pairs_this_rep)

            for actor, emotion in pairs_this_rep:
                approaches = ["polyglot_pg", "pure_mongo"]
                random.shuffle(approaches)

                for approach in approaches:
                    if approach == "polyglot_pg":
                        t, ids = pg_metadata_query(pg, actor, emotion)
                    else:
                        t, ids = mongo_metadata_query(mongo_db, actor, emotion)

                    w.writerow(["query","metadata_only",actor,emotion,None,
                                approach, rep, None, None,
                                f"{t:.6f}", len(ids), None])

            print(f"  Query rep {rep+1}/{REPS} done")

    # ── Phase 3: Read benchmark ────────────────────────────────────────────────
    print(f"\n── Phase 3: Reads ({len(query_pairs)} pairs x {REPS} reps) ──")

    with open(RESULTS_FILE, "a", newline="") as f:
        w = csv.writer(f)

        for rep in range(REPS):
            pairs_this_rep = list(query_pairs)
            random.shuffle(pairs_this_rep)

            for actor, emotion in pairs_this_rep:
                approaches = ["polyglot", "pure_mongo"]
                random.shuffle(approaches)
                results = []

                for approach in approaches:
                    if approach == "polyglot":
                        t, n, checksums = polyglot_e2e(pg, fs, actor, emotion)
                    else:
                        t, n, checksums = mongo_e2e(fs, mongo_db, actor, emotion)

                    if approach == "polyglot":
                        poly_checksums = checksums
                    else:
                        mongo_checksums = checksums
                    results.append((approach, t, n))

                checksum_ok = sorted(poly_checksums) == sorted(mongo_checksums)
                if not checksum_ok:
                    checksum_mismatches.append((actor, emotion))

                for approach, t, n in results:
                    w.writerow(["read","end_to_end",actor,emotion,None,
                                approach, rep, None, None,
                                f"{t:.6f}", n, checksum_ok])

            print(f"  Read rep {rep+1}/{REPS} done")

    pg.close()

    if checksum_mismatches:
        print(f"\n[WARNING] {len(checksum_mismatches)} checksum mismatches detected")
    else:
        print("\nData integrity check passed for all fetched files.")

    print(f"\nDone -> {RESULTS_FILE}")

if __name__ == "__main__":
    run()

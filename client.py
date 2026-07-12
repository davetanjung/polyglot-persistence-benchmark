"""
client.py — Benchmark PostgreSQL BYTEA vs MongoDB GridFS

Revisi dari versi sebelumnya:
  1. Filter ekstensi file (.DS_Store tidak lagi ikut terukur)
  2. Reset storage (TRUNCATE + delete_many) sebelum tiap run
  3. Kolom BYTEA diset STORAGE EXTERNAL (matikan kompresi TOAST)
  4. Warm-up phase sebelum pengukuran nyata
  5. Checksum SHA-256 untuk verifikasi integritas data
  6. Urutan DB (postgres/mongodb) diacak per operasi, bukan fixed pg->mongo
  7. GridFS chunk size didokumentasikan eksplisit (default 255 KB, tidak diubah)
"""

import csv
import hashlib
import random
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymongo
import gridfs


# ── Configuration ─────────────────────────────────────────────────────────────

DATA_DIRS: dict[str, Path] = {
    "audio_small":  Path("data/audio/300-400kb"),
    "video_medium": Path("data/video/5-6mb"),
    "video_large":  Path("data/video/10-17mb"),
}

VALID_EXTENSIONS = {".wav", ".mp4"}

REPS         = 10                              # repetitions per file
WARMUP_REPS  = 5                                # dummy ops per bucket before measuring
WARMUP_PAUSE_S = 30                             # pause after warm-up, before Phase 1
RESULTS_FILE = Path("results/raw_results.csv")

PG_DSN    = "host=localhost port=5433 dbname=mediadb user=postgres password=testpass"
MONGO_URI = "mongodb://localhost:27018/"

# GridFS chunk size: left at MongoDB default (255 KB / 261120 bytes).
# Not overridden — documented here so it's explicit rather than implicit.
GRIDFS_CHUNK_SIZE_BYTES = 261120


# ── Connect ────────────────────────────────────────────────────────────────────

def pg_connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(PG_DSN)
    return conn


def mongo_connect():
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client["mediadb"]
    fs = gridfs.GridFS(db)
    return db, fs


# ── Setup / reset ──────────────────────────────────────────────────────────────

def ensure_bytea_storage_external(conn):
    """
    Force STORAGE EXTERNAL on the BYTEA column so PostgreSQL does not attempt
    TOAST compression on data that is already compressed (.wav, .mp4).
    NOTE: SET STORAGE only affects rows inserted AFTER this call, so this
    must run before the table is (re)populated in reset_benchmark_storage().
    """
    cur = conn.cursor()
    cur.execute("ALTER TABLE media ALTER COLUMN content SET STORAGE EXTERNAL")
    conn.commit()
    cur.close()


def reset_benchmark_storage(conn, db):
    """
    Clear both stores before each run so repeated runs don't accumulate stale
    rows/files, which would make write/read latency comparisons unfair.
    """
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE media RESTART IDENTITY")
    conn.commit()
    cur.close()

    db.fs.files.delete_many({})
    db.fs.chunks.delete_many({})


# ── Checksum helper ────────────────────────────────────────────────────────────

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Postgres operations ────────────────────────────────────────────────────────

def pg_write(conn, data: bytes, stored_name: str, bucket: str, ext: str) -> float:
    """Insert BYTEA row. Returns elapsed seconds (commit included)."""
    cur = conn.cursor()
    t0  = time.perf_counter()
    cur.execute(
        "INSERT INTO media (filename, bucket, filetype, filesize_bytes, content) "
        "VALUES (%s, %s, %s, %s, %s)",
        (stored_name, bucket, ext, len(data), psycopg2.Binary(data)),
    )
    conn.commit()
    t1 = time.perf_counter()
    cur.close()
    return t1 - t0


def pg_read(conn, stored_name: str) -> tuple[float, bytes]:
    """Fetch BYTEA row by filename. Returns (elapsed_s, data)."""
    cur = conn.cursor()
    t0  = time.perf_counter()
    cur.execute(
        "SELECT content FROM media WHERE filename = %s",
        (stored_name,),
    )
    row  = cur.fetchone()
    data = bytes(row[0])
    t1   = time.perf_counter()
    cur.close()
    return t1 - t0, data


# ── MongoDB operations ─────────────────────────────────────────────────────────

def mongo_write(fs: gridfs.GridFS, data: bytes, stored_name: str,
                bucket: str, ext: str) -> float:
    """Put bytes into GridFS. Returns elapsed seconds."""
    t0 = time.perf_counter()
    fs.put(data, filename=stored_name, bucket=bucket, filetype=ext)
    t1 = time.perf_counter()
    return t1 - t0


def mongo_read(fs: gridfs.GridFS, stored_name: str) -> tuple[float, bytes]:
    """Retrieve GridFS file by filename. Returns (elapsed_s, data)."""
    t0       = time.perf_counter()
    grid_out = fs.find_one({"filename": stored_name})
    data     = grid_out.read()
    t1       = time.perf_counter()
    return t1 - t0, data


# ── File collection ────────────────────────────────────────────────────────────

def collect_files(data_dirs: dict[str, Path]) -> list[tuple[str, Path]]:
    entries = []
    for bucket, dirpath in data_dirs.items():
        if not dirpath.exists():
            print(f"[WARN] Directory not found: {dirpath} — skipping")
            continue
        files = sorted(
            f for f in dirpath.iterdir()
            if f.is_file() and f.suffix in VALID_EXTENSIONS
        )
        if not files:
            print(f"[WARN] No valid files in {dirpath} — skipping")
            continue
        print(f"  {bucket}: {len(files)} files")
        entries.extend((bucket, fp) for fp in files)
    return entries


# ── Warm-up ─────────────────────────────────────────────────────────────────────

def warmup(conn, db, fs, files: list[tuple[str, Path]]):
    """
    Run a handful of unmeasured write+read cycles per bucket to stabilize
    filesystem and database caches before real measurements begin. Warm-up
    writes are cleaned up afterward so they don't pollute Phase 1/2 results.
    """
    print(f"\n── Warm-up ({WARMUP_REPS} ops per bucket) ──")
    seen_buckets = set()

    for bucket, filepath in files:
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)

        raw = filepath.read_bytes()
        for i in range(WARMUP_REPS):
            name = f"__warmup_{bucket}_{i}{filepath.suffix}"
            pg_write(conn, raw, name, "warmup", filepath.suffix)
            mongo_write(fs, raw, name, "warmup", filepath.suffix)
            pg_read(conn, name)
            mongo_read(fs, name)

    # Clean up warm-up data so it doesn't affect Phase 1/2 measurements
    cur = conn.cursor()
    cur.execute("DELETE FROM media WHERE bucket = 'warmup'")
    conn.commit()
    cur.close()
    db.fs.files.delete_many({"bucket": "warmup"})
    db.fs.chunks.delete_many({})

    print(f"  Warm-up done. Pausing {WARMUP_PAUSE_S}s before Phase 1...")
    time.sleep(WARMUP_PAUSE_S)


# ── Main ────────────────────────────────────────────────────────────────────────

def run() -> None:
    RESULTS_FILE.parent.mkdir(exist_ok=True)

    print("Connecting to databases...")
    try:
        pg = pg_connect()
    except Exception as e:
        raise SystemExit(f"[ERROR] Cannot connect to PostgreSQL: {e}")

    try:
        db, fs = mongo_connect()
    except Exception as e:
        raise SystemExit(f"[ERROR] Cannot connect to MongoDB: {e}")

    print("Setting BYTEA column storage to EXTERNAL (disable TOAST compression)...")
    ensure_bytea_storage_external(pg)

    print("Resetting benchmark storage...")
    reset_benchmark_storage(pg, db)

    print("\nCollecting files...")
    files = collect_files(DATA_DIRS)
    if not files:
        raise SystemExit("[ERROR] No valid files found. Check DATA_DIRS paths.")

    warmup(pg, db, fs, files)

    # Build (bucket, filepath, repetition) combos
    combos = [
        (bucket, fp, r)
        for bucket, fp in files
        for r in range(REPS)
    ]
    random.shuffle(combos)

    total_ops = len(combos) * 4          # 2 DBs × 2 operations
    print(f"\n{len(files)} unique files × {REPS} reps = {len(combos)} combos")
    print(f"Total measurements: {total_ops}")

    # ── Phase 1: Writes ────────────────────────────────────────────────────────
    print("\n── Phase 1: Writes ──")

    read_queue: list[tuple[str, str, int, str]] = []   # (bucket, stored_name, filesize, checksum)
    checksum_mismatches = []

    with open(RESULTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "bucket", "stored_name", "filesize_bytes",
            "db", "operation", "rep", "elapsed_seconds",
        ])

        for i, (bucket, filepath, rep) in enumerate(combos):
            # Load bytes OUTSIDE timer — we measure DB, not disk
            raw         = filepath.read_bytes()
            filesize    = len(raw)
            stored_name = f"{filepath.stem}_r{rep:02d}{filepath.suffix}"
            ext         = filepath.suffix
            checksum    = sha256(raw)

            # Randomize DB order per operation to avoid systematic order bias
            ops = ["postgres", "mongodb"]
            random.shuffle(ops)

            for db_name in ops:
                if db_name == "postgres":
                    elapsed = pg_write(pg, raw, stored_name, bucket, ext)
                else:
                    elapsed = mongo_write(fs, raw, stored_name, bucket, ext)
                writer.writerow([bucket, stored_name, filesize, db_name, "write", rep, f"{elapsed:.6f}"])

            read_queue.append((bucket, stored_name, filesize, checksum))

            if (i + 1) % 20 == 0 or (i + 1) == len(combos):
                print(f"  Writes: {i+1}/{len(combos)}")

    # ── Phase 2: Reads ─────────────────────────────────────────────────────────
    print("\n── Phase 2: Reads ──")
    random.shuffle(read_queue)           # re-randomize so read order != write order

    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)

        for i, (bucket, stored_name, filesize, orig_checksum) in enumerate(read_queue):
            ops = ["postgres", "mongodb"]
            random.shuffle(ops)

            for db_name in ops:
                if db_name == "postgres":
                    elapsed, data = pg_read(pg, stored_name)
                else:
                    elapsed, data = mongo_read(fs, stored_name)

                if sha256(data) != orig_checksum:
                    checksum_mismatches.append((db_name, stored_name))

                writer.writerow([bucket, stored_name, filesize, db_name, "read", 0, f"{elapsed:.6f}"])

            if (i + 1) % 20 == 0 or (i + 1) == len(read_queue):
                print(f"  Reads:  {i+1}/{len(read_queue)}")

    pg.close()

    if checksum_mismatches:
        print(f"\n[WARNING] {len(checksum_mismatches)} checksum mismatches detected:")
        for db_name, name in checksum_mismatches:
            print(f"    {db_name}: {name}")
    else:
        print("\nData integrity check passed: all checksums matched.")

    print(f"Done. Results saved -> {RESULTS_FILE}")


if __name__ == "__main__":
    run()

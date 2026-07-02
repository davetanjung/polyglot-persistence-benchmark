import csv
import random
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymongo
import gridfs


# configuration
DATA_DIRS: dict[str, Path] = {
    "audio_small":  Path("data/audio/300-400kb"),
    "video_medium": Path("data/video/1-2mb"),
    "video_large":  Path("data/video/5-6mb"),
}

REPS         = 10                              # repetitions per file
RESULTS_FILE = Path("results/raw_results.csv")

PG_DSN    = "host=localhost port=5433 dbname=mediadb user=postgres password=testpass"
MONGO_URI = "mongodb://localhost:27018/"



# connect
def pg_connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(PG_DSN)
    # autocommit=False (default) — explicit commit after each insert
    return conn


def mongo_connect() -> gridfs.GridFS:
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")          # fail fast if Mongo is not up
    db = client["mediadb"]
    return gridfs.GridFS(db)


# postgres operation
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


def pg_read(conn, stored_name: str) -> tuple[float, int]:
    """Fetch BYTEA row by filename. Returns (elapsed_s, byte_count)."""
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
    return t1 - t0, len(data)


# mongodb operations
def mongo_write(fs: gridfs.GridFS, data: bytes, stored_name: str,
                bucket: str, ext: str) -> float:
    """Put bytes into GridFS. Returns elapsed seconds."""
    t0 = time.perf_counter()
    fs.put(data, filename=stored_name, bucket=bucket, filetype=ext)
    t1 = time.perf_counter()
    return t1 - t0


def mongo_read(fs: gridfs.GridFS, stored_name: str) -> tuple[float, int]:
    """Retrieve GridFS file by filename. Returns (elapsed_s, byte_count).
    Uses find_one (same lookup semantics as Postgres filename index scan).
    """
    t0       = time.perf_counter()
    grid_out = fs.find_one({"filename": stored_name})
    data     = grid_out.read()
    t1       = time.perf_counter()
    return t1 - t0, len(data)


# file collection 

def collect_files(data_dirs: dict[str, Path]) -> list[tuple[str, Path]]:
    entries = []
    for bucket, dirpath in data_dirs.items():
        if not dirpath.exists():
            print(f"[WARN] Directory not found: {dirpath} — skipping")
            continue
        files = sorted(f for f in dirpath.iterdir() if f.is_file())
        if not files:
            print(f"[WARN] No files in {dirpath} — skipping")
            continue
        print(f"  {bucket}: {len(files)} files")
        entries.extend((bucket, fp) for fp in files)
    return entries


def run() -> None:
    RESULTS_FILE.parent.mkdir(exist_ok=True)

    print("Connecting to databases...")
    try:
        pg = pg_connect()
    except Exception as e:
        raise SystemExit(f"[ERROR] Cannot connect to PostgreSQL: {e}")

    try:
        fs = mongo_connect()
    except Exception as e:
        raise SystemExit(f"[ERROR] Cannot connect to MongoDB: {e}")

    print("\nCollecting files...")
    files = collect_files(DATA_DIRS)
    if not files:
        raise SystemExit("[ERROR] No files found. Check DATA_DIRS paths.")

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

    # phase 1: write
    print("\n── Phase 1: Writes ──")

    # stored_name is the key used by both DBs in read phase
    read_queue: list[tuple[str, str, int]] = []   # (bucket, stored_name, filesize)

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

            # Postgres write
            pg_t = pg_write(pg, raw, stored_name, bucket, ext)
            writer.writerow([bucket, stored_name, filesize, "postgres", "write", rep, f"{pg_t:.6f}"])

            # Mongo write
            mg_t = mongo_write(fs, raw, stored_name, bucket, ext)
            writer.writerow([bucket, stored_name, filesize, "mongodb", "write", rep, f"{mg_t:.6f}"])

            read_queue.append((bucket, stored_name, filesize))

            if (i + 1) % 20 == 0 or (i + 1) == len(combos):
                print(f"  Writes: {i+1}/{len(combos)}")

    # phase 2:
    print("\n── Phase 2: Reads ──")
    random.shuffle(read_queue)           # re-randomize so read order ≠ write order

    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)

        for i, (bucket, stored_name, filesize) in enumerate(read_queue):
            # Postgres read — index scan by filename
            pg_t, pg_size = pg_read(pg, stored_name)
            writer.writerow([bucket, stored_name, filesize, "postgres", "read", 0, f"{pg_t:.6f}"])

            # Mongo read — GridFS find_one by filename (same semantics)
            mg_t, mg_size = mongo_read(fs, stored_name)
            writer.writerow([bucket, stored_name, filesize, "mongodb", "read", 0, f"{mg_t:.6f}"])

            if (i + 1) % 20 == 0 or (i + 1) == len(read_queue):
                print(f"  Reads:  {i+1}/{len(read_queue)}")

    pg.close()
    print(f"\nDone. Results saved → {RESULTS_FILE}")


if __name__ == "__main__":
    run()
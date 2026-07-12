# Multimedia Database Benchmark

Benchmark project for comparing PostgreSQL and MongoDB storage patterns for
audio/video files.

- **Experiment 1:** PostgreSQL `BYTEA` vs MongoDB GridFS
- **Experiment 2:** Polyglot persistence, with PostgreSQL metadata and MongoDB
  GridFS binary storage, compared against pure MongoDB GridFS with embedded
  metadata

Dataset files are based on RAVDESS-style audio/video naming where available.
The `10-17mb` video folder contains filenames without metadata, so
`polyglot/client.py` generates deterministic metadata for that bucket.

---

## Project Structure

```text
experiment/
├── docker-compose.yaml              # PostgreSQL 16 and MongoDB 7 services
├── init.sql/                        # Present in repo; docker-compose mounts this path
├── readme.md
├── .gitignore
│
├── client.py                        # Experiment 1: PostgreSQL BYTEA vs MongoDB GridFS
├── analyze.py                       # Experiment 1 analysis
│
├── polyglot/
│   ├── client.py                    # Experiment 2: PG metadata + Mongo binary vs pure Mongo
│   └── analyze.py                   # Experiment 2 analysis
│
├── sql/
│   ├── base.sql                     # PostgreSQL schema for Experiment 1
│   └── polyglot_schema.sql          # PostgreSQL schema for Experiment 2
│
├── data/
│   ├── audio/
│   │   └── 300-400kb/               # Audio files used by both experiments
│   └── video/
│       ├── 5-6mb/                   # Video files with RAVDESS-style metadata names
│       └── 10-17mb/                 # Large video files without filename metadata
│
└── results/
    ├── raw_results.csv              # Experiment 1 raw measurements
    ├── summary.csv                  # Experiment 1 summary
    ├── polyglot_results.csv         # Experiment 2 raw measurements
    └── polyglot_summary.csv         # Experiment 2 summary
```

Notes:
- `__pycache__/` and `.DS_Store` are ignored by `.gitignore`.
- `polyglot/__pycache__/` may appear locally after running Python.
- `client.py` currently references `data/video/1-2mb` for `video_medium`, but
  the current data tree only contains `data/video/5-6mb` and `data/video/10-17mb`.
  Update `DATA_DIRS` in `client.py` or restore the `1-2mb` folder before running
  Experiment 1.

---

## Environment

| Component  | Configuration |
|------------|---------------|
| PostgreSQL | Docker service `postgres-test`, image `postgres:16`, host port `5433` |
| MongoDB | Docker service `mongo-test`, image `mongo:7`, host port `27018` |
| Database | `mediadb` |
| PostgreSQL user | `postgres` |
| PostgreSQL password | `testpass` |
| Python packages | `psycopg2-binary`, `pymongo`, `pandas` |

---

## Setup

### 1. Install Python Dependencies

```bash
pip install psycopg2-binary pymongo pandas
```

### 2. Start Databases

```bash
docker compose up -d
```

PostgreSQL is exposed on `localhost:5433`.
MongoDB is exposed on `localhost:27018`.

### 3. Load PostgreSQL Schemas

From the repository root:

```bash
docker exec -i experiment-postgres-test-1 psql -U postgres -d mediadb < sql/base.sql
docker exec -i experiment-postgres-test-1 psql -U postgres -d mediadb < sql/polyglot_schema.sql
```

Verify tables:

```bash
docker exec -it experiment-postgres-test-1 psql -U postgres -d mediadb -c "\dt"
```

Expected tables:

```text
media
media_meta
```

---

## Running Experiments

### Experiment 1: PostgreSQL BYTEA vs MongoDB GridFS

From the repository root:

```bash
python client.py
python analyze.py
```

This experiment measures:
- write latency
- read latency
- checksum validation for fetched bytes
- per-bucket latency and throughput summaries

Before each run, `client.py` resets:
- PostgreSQL table `media`
- MongoDB database `mediadb` GridFS collections

It also runs a warm-up phase before measured operations.

### Experiment 2: Polyglot Persistence vs Pure MongoDB

From the repository root:

```bash
cd polyglot
python client.py
python analyze.py
```

This experiment has three measured phases:

| Phase | Meaning | Approaches compared |
|-------|---------|---------------------|
| `write` | Store metadata and/or binary | `polyglot` vs `pure_mongo` |
| `query` | Metadata-only lookup | `polyglot_pg` vs `pure_mongo` |
| `read` | Metadata lookup plus file fetch | `polyglot` vs `pure_mongo` |

Before each run, `polyglot/client.py` resets:
- PostgreSQL table `media_meta`
- MongoDB database `mediadb_poly` GridFS collections

`polyglot/analyze.py` checks that query/read comparisons touch the same number
of files for both approaches. If `n_files` differs, analysis stops instead of
producing an unfair paper table.

---

## Dataset Folders

| Script bucket | Current folder | Notes |
|---------------|----------------|-------|
| `audio_small` | `data/audio/300-400kb` | RAVDESS-style `.wav` files |
| `video_medium` in `polyglot/client.py` | `data/video/5-6mb` | RAVDESS-style `.mp4` files |
| `video_large` in `polyglot/client.py` | `data/video/10-17mb` | Large `.mp4` files without filename metadata |
| `video_medium` in root `client.py` | `data/video/1-2mb` | Referenced by code, not present in current tree |
| `video_large` in root `client.py` | `data/video/5-6mb` | RAVDESS-style `.mp4` files |

Valid file extensions are `.wav` and `.mp4`.

---

## RAVDESS Filename Convention

Files with RAVDESS-style metadata names use:

```text
modality-channel-emotion-intensity-statement-repetition-actor.ext
```

Example:

```text
03-01-03-02-01-01-07.wav
│  │  │  │  │  │  └─ actor
│  │  │  │  │  └──── repetition
│  │  │  │  └─────── statement
│  │  │  └────────── intensity
│  │  └───────────── emotion
│  └──────────────── channel
└─────────────────── modality
```

For files that do not match this convention, `polyglot/client.py` only accepts
them in the `video_large` bucket and assigns deterministic generated metadata.

---

## Results

Experiment 1 writes:

```text
results/raw_results.csv
results/summary.csv
```

Experiment 2 writes:

```text
results/polyglot_results.csv
results/polyglot_summary.csv
```

Current result files may be stale after code or dataset changes. Regenerate them
after changing folder contents, schema, benchmark phases, or metadata handling.

---

## Stop Containers

```bash
docker compose down
```

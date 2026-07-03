# Multimedia Database Benchmark
**Comparing PostgreSQL BYTEA vs MongoDB GridFS for Audio-Visual File Storage**
Dataset: RAVDESS (Ryerson Audio-Visual Database of Emotional Speech and Song)

---

## Project Structure

```
experiment/
├── docker-compose.yaml         # PostgreSQL 16 + MongoDB 7 containers
├── init.sql                    # PostgreSQL schema for pure benchmark
├── polyglot_schema.sql         # PostgreSQL schema for polyglot experiment
│
├── client.py                   # Experiment 1: pure PG vs pure Mongo
├── analyze.py                  # Analysis for Experiment 1
│
├── polyglot/
│   ├── client.py               # Experiment 2: polyglot (PG meta + Mongo binary)
│   └── analyze.py              # Analysis for Experiment 2
│
├── data/
│   ├── audio/
│   │   └── 300-400kb/          # RAVDESS .wav files (original, ~375KB avg)
│   └── video/
│       ├── 1-2mb/              # RAVDESS .mp4 re-encoded at 2Mbps (libx264)
│       └── 5-6mb/              # RAVDESS .mp4 original files
│
├── results/
│   ├── raw_results.csv         # Experiment 1 raw output
│   ├── summary.csv             # Experiment 1 summary stats
│   ├── polyglot_results.csv    # Experiment 2 raw output
│   └── polyglot_summary.csv    # Experiment 2 summary stats
│
└── sql/                        # Additional SQL scripts if any
```

---

## Environment

| Component     | Version          |
|---------------|------------------|
| Python        | 3.9.6 (pyenv)    |
| PostgreSQL    | 16 (Docker)      |
| MongoDB       | 7 (Docker)       |
| Hardware      | MacBook Pro M2   |
| RAM           | 8GB              |
| OS            | macOS (ARM64)    |

---

## Setup

### 1. Prerequisites
```bash
pip install psycopg2-binary pymongo pandas
```

### 2. Start containers
```bash
# From experiment/ root
docker compose up -d
```

> **Note:** PostgreSQL maps to host port `5433` (not 5432) to avoid conflict
> with existing local PostgreSQL. MongoDB maps to `27018`.

### 3. Verify containers
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected: `experiment-postgres-test-1` and `experiment-mongo-test-1` both `Up`.

### 4. Load schemas
```bash
# Pure benchmark schema
docker exec -i experiment-postgres-test-1 psql -U postgres -d mediadb < init.sql

# Polyglot schema
docker exec -i experiment-postgres-test-1 psql -U postgres -d mediadb < polyglot_schema.sql

# Verify
docker exec -it experiment-postgres-test-1 psql -U postgres -d mediadb -c "\dt"
```

---

## Running Experiments

### Experiment 1 — Pure Benchmark (PG BYTEA vs MongoDB GridFS)

```bash
# From experiment/ root
python client.py
python analyze.py
```

Measures write and read latency for direct BLOB storage across three file size buckets.

### Experiment 2 — Polyglot (PG metadata + MongoDB binary)

```bash
# From experiment/polyglot/
cd polyglot
python client.py
python analyze.py
```

Measures:
- Write latency: polyglot (dual-write) vs pure MongoDB
- Q1: metadata-only query latency: PG index scan vs MongoDB document filter
- Q2: end-to-end latency: PG query + Mongo fetch vs pure MongoDB

---

## Dataset

**RAVDESS** — Ryerson Audio-Visual Database of Emotional Speech and Song
Source: [Zenodo](https://zenodo.org/record/1188976)

### File buckets

| Bucket         | Type    | Size      | Source                          |
|----------------|---------|-----------|---------------------------------|
| `audio_small`  | `.wav`  | 300–400KB | RAVDESS original audio          |
| `video_medium` | `.mp4`  | 1–2MB     | RAVDESS re-encoded at 2Mbps bitrate (`libx264`, full duration) |
| `video_large`  | `.mp4`  | 5–6MB     | RAVDESS original video          |

> **Methodology note:** `video_medium` files are full-duration re-encodes of
> `video_large` at reduced bitrate — not duration-trimmed clips. The bucket
> represents compression level difference, not duration difference.
> Original RAVDESS filenames are preserved, so metadata labels remain valid.

### RAVDESS filename convention
```
03-01-03-02-01-01-07.wav
│  │  │  │  │  │  └─ actor (01–24)
│  │  │  │  │  └──── repetition (01–02)
│  │  │  │  └─────── statement (01–02)
│  │  │  └────────── intensity (01=normal, 02=strong)
│  │  └───────────── emotion (01=neutral, 02=calm, 03=happy, 04=sad,
│  │                          05=angry, 06=fearful, 07=disgust, 08=surprised)
│  └──────────────── channel (01=speech, 02=song)
└─────────────────── modality (01=audio-only, 02=video-only, 03=audio-video)
```

---

## Known Limitations

- Experiments run on consumer hardware (MacBook Pro M2, 8GB RAM) with Docker Desktop VM overhead — results are not equivalent to isolated server benchmarks
- Docker Desktop resource allocation: CPU 4 cores, Memory 5GB assigned to VM
- All other Docker containers (Odoo stack) must be stopped before running experiments to avoid memory pressure and I/O noise
- `video_medium` bucket uses re-encoded files, not original RAVDESS video — compression artifact behavior may differ from source files

---

## Stop containers after experiment
```bash
docker compose down

# Restart Odoo stack if needed
docker start odoo-app odoo-db docker-mongo-1
```
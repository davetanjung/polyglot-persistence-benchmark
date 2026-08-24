# Multimedia Database Benchmark (Polyglot Persistence)

Benchmark project for comparing PostgreSQL and MongoDB storage patterns for audio/video files.

## 1. Goal of the Experiment
The primary goal of this research is to empirically benchmark three different database storage strategies for handling multimedia files (Audio and Video) and their associated metadata:
1. **Pure PostgreSQL (`pg_bytea`)**: Storing both metadata and binary files in PostgreSQL using the `BYTEA` column.
2. **Pure MongoDB (`mongo_gridfs`)**: Storing both binary files and metadata in MongoDB GridFS using embedded document fields.
3. **Polyglot Persistence (`polyglot`)**: A hybrid architecture using PostgreSQL for structured metadata (B-tree indexing) and MongoDB GridFS for heavy binary multimedia content, optimized with parallel writes.

## 2. Optimizations Implemented
- **Parallel Writes (Concurrency)**: The Polyglot strategy uses `concurrent.futures.ThreadPoolExecutor` to simultaneously write to MongoDB GridFS and PostgreSQL, eliminating the theoretical "double-write" penalty.
- **Machine Learning Database Tuning (Optuna)**: Hyperparameters like MongoDB `chunk_size_bytes` and PostgreSQL memory settings (`shared_buffers`, `work_mem`) are optimized via Optuna to find the absolute best combination of memory and storage parameters before running the benchmark.

## 3. Project Structure

```text
experiment/
├── docker-compose.yaml              # PostgreSQL 16 and MongoDB 7 services
├── readme.md                        # Project documentation
├── final_experiment_summary.md      # Summary of academic findings
│
├── main.py                          # Unified benchmark script (runs 3 phases)
├── optimize.py                      # Optuna hyperparameter tuning script
├── analyze.py                       # Statistical analysis (Wilcoxon, Mann-Whitney U, Cohen's d)
├── plot_results.ipynb               # Jupyter Notebook for visualization
│
├── data/                            # Dataset folders (RAVDESS / Tsinghua FIB Lab)
│   ├── audio/
│   │   ├── 300-400kb/
│   │   └── 500-600kb/
│   └── video/
│       ├── 5-6mb/
│       └── 10-17mb/
│
└── results/                         # Generated outputs
    ├── benchmark_results.csv        # Raw metrics from main.py
    ├── summary.csv                  # Statistical summary from analyze.py
    └── plots/                       # High-resolution Box Plots
```

## 4. Environment Setup

### Install Python Dependencies
```bash
pip install psycopg2-binary pymongo pandas optuna
```

### Start Databases
```bash
docker compose up -d
```
- PostgreSQL is exposed on `localhost:5433`.
- MongoDB is exposed on `localhost:27018`.
- *Note: Schema creation is handled automatically by `main.py` at runtime. You do not need to manually load any SQL scripts.*

## 5. Running the Pipeline

The entire experimental workflow is broken down into a clean, reproducible engineering pipeline:

### Step 1: Hyperparameter Tuning
Run Optuna trials to find the best database configurations. This modifies PostgreSQL configs via `ALTER SYSTEM` and generates `tuning_config.json`.
```bash
python optimize.py
```

### Step 2: Core Benchmark
Run the unified benchmark script. It loads the `tuning_config.json`, cleans the storage, runs a warm-up phase, and executes the 3-phase benchmark (Write, Metadata Query, End-to-End Retrieval) across 10 randomized repetitions.
```bash
python main.py
```
Outputs are saved to `results/benchmark_results.csv`.

### Step 3: Statistical Analysis
Read the raw metrics to compute statistical tests (Wilcoxon signed-rank, Mann-Whitney U, Cohen's d).
```bash
python analyze.py
```
Exports `results/summary.csv` and `results/statistical_tests.csv`.

### Step 4: Visualization
Open `plot_results.ipynb` in Jupyter/VSCode to visualize the raw data into academic, high-resolution Box Plots (saved in `results/plots/`).

## 6. Dataset and Filename Convention

Files with RAVDESS-style metadata names use:
```text
modality-channel-emotion-intensity-statement-repetition-actor.ext
```
For files that do not match this convention (e.g., in `video_medium`), `main.py` assigns deterministic generated metadata on the fly.

## 7. Stop Containers
```bash
docker compose down
```

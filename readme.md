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
- **Hardware Scalability Testing**: Automated testing across multiple Docker container resource limits (e.g., 4 CPU / 4GB RAM, 6 CPU / 4.5GB RAM) to observe how approaches scale.

## 3. Project Structure

```text
experiment/
├── docker-compose.yaml              # PostgreSQL 16 and MongoDB 7 services
├── readme.md                        # Project documentation
│
├── main.py                          # Unified benchmark script (runs 3 phases)
├── optimize.py                      # Optuna hyperparameter tuning script
├── analyze.py                       # Statistical analysis (Wilcoxon, Mann-Whitney U, Cohen's d)
├── run_experiments.py               # Automated pipeline to run benchmarks across hardware limits
├── plot_results.ipynb               # Jupyter Notebook for visualization
├── tuning_config.json               # Active hyperparameter configuration
│
├── results/                         # Generated outputs
│   ├── benchmark_*cpu*.csv          # Raw metrics from main.py per hardware limit
│   ├── summary.csv                  # Statistical summary from analyze.py
│   ├── statistical_tests_*.csv      # Intra-DB and Inter-HW comparisons
│   ├── tuning_*cpu*.json            # Saved tuning configurations per hardware limit
│   └── plots/                       # High-resolution Box Plots
│
├── gambar/                          # Image assets directory
└── sql/                             # SQL related assets or backups
```

*(Note: Data generation scripts, drafting documents, and datasets have been excluded from version control).*

## 4. Environment Setup

### Install Python Dependencies
```bash
pip install psycopg2-binary pymongo pandas numpy scipy optuna tqdm
```

### Start Databases
```bash
docker compose up -d
```
- PostgreSQL is exposed on `localhost:5433`.
- MongoDB is exposed on `localhost:27018`.
- *Note: Schema creation is handled automatically by `main.py` at runtime.*

## 5. Running the Pipeline

The entire experimental workflow is broken down into a reproducible engineering pipeline.

### Option A: Automated Multi-Hardware Benchmark (Recommended)
This script loops through different CPU and Memory limits, automatically restarting the Docker containers, running the Optuna tuning, and then running the core benchmark.
```bash
python run_experiments.py
```
*Outputs are saved individually per hardware limit in `results/benchmark_*cpu*.csv` and `results/tuning_*cpu*.json`.*

### Option B: Manual Execution
If you prefer running a single step manually:

**Step 1: Hyperparameter Tuning**
Find the best database configurations for the current environment. Generates `tuning_config.json`.
```bash
python optimize.py
```

**Step 2: Core Benchmark**
Executes the 3-phase benchmark (Write, Metadata Query, End-to-End Retrieval) across 10 randomized repetitions.
```bash
python main.py
```

### Step 3: Statistical Analysis
Read the raw metrics from the `results/` folder to compute statistical tests (Wilcoxon signed-rank, Mann-Whitney U, Cohen's d). It analyzes both Intra-Hardware (comparing databases) and Inter-Hardware (scalability of a database).
```bash
python analyze.py
```
*Exports `results/summary.csv`, `results/statistical_tests_intra_db.csv`, and `results/statistical_tests_inter_hw.csv`.*

### Step 4: Visualization
Open `plot_results.ipynb` in Jupyter/VSCode to visualize the raw data into academic, high-resolution Box Plots.

## 6. Dataset and Filename Convention

Files with RAVDESS-style metadata names use:
```text
modality-channel-emotion-intensity-statement-repetition-actor.ext
```
For files that do not match this convention (e.g., in `video_medium`), `main.py` assigns deterministic generated metadata on the fly.

## 7. Stop Containers
```bash
docker compose down -v
```

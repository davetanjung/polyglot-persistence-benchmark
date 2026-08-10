import json
import optuna
import subprocess
import time
from pathlib import Path
import psycopg2
import pymongo

from main import (
    DATA_DIRS, 
    connect, 
    collect_files, 
    reset_benchmark_storage,
    polyglot_write,
    polyglot_meta_query,
    polyglot_e2e,
    setup_postgres_tables
)

# File to output the best parameters
CONFIG_FILE = Path("tuning_config.json")

def restart_postgres():
    """Restarts the postgres container and waits for it to be ready."""
    print("Restarting postgres-test container to apply config...")
    compose_dir = Path(__file__).parent
    subprocess.run(["docker", "compose", "restart", "postgres-test"], cwd=compose_dir, check=True)
    
    # Wait for postgres to be ready
    retries = 15
    for i in range(retries):
        try:
            conn = psycopg2.connect("host=localhost port=5433 dbname=mediadb user=postgres password=testpass")
            conn.close()
            print("Postgres is ready.")
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Postgres failed to start within the timeout.")

def objective(trial):
    # 1. Suggest hyperparameters
    # MongoDB
    chunk_size_bytes = trial.suggest_categorical(
        "chunk_size_bytes", 
        [128 * 1024, 255 * 1024, 512 * 1024, 1024 * 1024, 2 * 1024 * 1024, 4 * 1024 * 1024]
    )

    # PostgreSQL
    shared_buffers = trial.suggest_categorical("shared_buffers", ["128MB", "256MB", "512MB", "1GB"])
    work_mem = trial.suggest_categorical("work_mem", ["4MB", "16MB", "64MB"])
    maintenance_work_mem = trial.suggest_categorical("maintenance_work_mem", ["64MB", "128MB", "256MB"])
    random_page_cost = trial.suggest_categorical("random_page_cost", ["1.1", "4.0"])
    
    print(f"\n--- Starting Trial {trial.number} ---")
    
    # 2. Apply Postgres settings
    pg, _, _ = connect()
    pg.autocommit = True
    cur = pg.cursor()
    cur.execute(f"ALTER SYSTEM SET shared_buffers = '{shared_buffers}'")
    cur.execute(f"ALTER SYSTEM SET work_mem = '{work_mem}'")
    cur.execute(f"ALTER SYSTEM SET maintenance_work_mem = '{maintenance_work_mem}'")
    cur.execute(f"ALTER SYSTEM SET random_page_cost = '{random_page_cost}'")
    cur.close()
    pg.close()
    
    # Restart Postgres to apply ALTER SYSTEM (specifically shared_buffers requires restart)
    restart_postgres()
    
    # 3. Reconnect and setup
    pg, fs, mongo_db = connect()

    setup_postgres_tables(pg)
    reset_benchmark_storage(pg, mongo_db)
    
    # 4. Load a tiny subset of files for the trial (e.g. 5 video medium files)
    all_files = collect_files(DATA_DIRS)
    # Filter for a few heavy files to really test the memory/chunking
    test_files = [f for f in all_files if f[0] == "video_medium"][:5]
    if not test_files:
        test_files = all_files[:5]
        
    total_latency = 0.0
    
    # Test Write
    print(f"Testing {len(test_files)} writes...")
    import main
    # Temporarily override the global chunk size in main for polyglot_write to use
    original_chunk_size = main.GRIDFS_CHUNK_SIZE_BYTES
    main.GRIDFS_CHUNK_SIZE_BYTES = chunk_size_bytes

    actor_emotions = set()
    for bucket, filepath, meta in test_files:
        ext = filepath.suffix
        stored_name = f"optuna_test_{bucket}_{meta['actor']}_{meta['emotion']}{ext}"
        lat = polyglot_write(pg, fs, filepath, stored_name, bucket, meta, ext)
        total_latency += lat
        actor_emotions.add((meta["actor"], meta["emotion"]))
        
    main.GRIDFS_CHUNK_SIZE_BYTES = original_chunk_size
        
    # Test Meta Query
    print(f"Testing {len(actor_emotions)} meta queries...")
    for actor, emotion in actor_emotions:
        lat, _ = polyglot_meta_query(pg, actor, emotion)
        total_latency += lat
        
    # Test Read
    print(f"Testing {len(actor_emotions)} e2e reads...")
    for actor, emotion in actor_emotions:
        lat, _, _ = polyglot_e2e(pg, fs, actor, emotion)
        total_latency += lat
        
    pg.close()
    
    print(f"Trial {trial.number} finished with total latency: {total_latency:.4f}s")
    return total_latency

if __name__ == "__main__":
    print("Initializing Optuna Database Tuning...")
    study = optuna.create_study(direction="minimize")
    
    # Limit trials to a small number since it restarts a container
    try:
        study.optimize(objective, n_trials=10)
    except KeyboardInterrupt:
        print("\nOptimization interrupted by user. Saving current best params...")

    print("\n==================================================")
    print("Optimization Complete!")
    print("Best parameters:")
    best_params = study.best_params
    for key, value in best_params.items():
        print(f"  {key}: {value}")
        
    # Save the best parameters to a JSON file
    with open(CONFIG_FILE, "w") as f:
        json.dump(best_params, f, indent=4)
        
    print(f"\nSaved optimal configuration to {CONFIG_FILE}")
    print("You can now run `python main.py` and it will automatically use these settings.")

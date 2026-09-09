import os
import subprocess
import time

# Note: Your Docker Desktop VM maxes out at 4.8 GB RAM based on `docker info`.
# The 6 CPU configuration is set to use 4.5g to ensure it doesn't crash Docker.
CONFIGS = [
    #{"cpu": "1", "mem": "1g"},
    #{"cpu": "2", "mem": "2g"},
    {"cpu": "4", "mem": "4g"},
    {"cpu": "6", "mem": "4.5g"}
]

def run_command(cmd, env=None):
    print(f"\n>>> Running: {cmd}")
    env_vars = os.environ.copy()
    if env:
        env_vars.update(env)
    subprocess.run(cmd, shell=True, check=True, env=env_vars)

def wait_for_postgres():
    print("\nWaiting for PostgreSQL to fully initialize...")
    for _ in range(30):
        try:
            # Check if postgres is ready inside the container
            subprocess.run(
                "docker exec experiment-postgres-test-1 pg_isready -U postgres", 
                shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print("PostgreSQL is ready!")
            time.sleep(2) # Give it 2 extra seconds just to be safe
            return
        except subprocess.CalledProcessError:
            time.sleep(1)
    print("WARNING: PostgreSQL took too long to initialize!")

def main():
    for conf in CONFIGS:
        c = conf['cpu']
        m = conf['mem']
        
        print(f"\n{'='*50}")
        print(f"Starting Experiment: {c} CPUs, {m} Memory")
        print(f"{'='*50}")
        
        env = {
            "DB_CPUS": c,
            "DB_MEM": m,
            "RESULTS_FILENAME": f"results/benchmark_{c}cpu_{m}.csv"
        }
        
        # 1. Reset Docker with new limits
        run_command("docker compose down -v", env=env)
        run_command("docker compose up -d", env=env)
        
        wait_for_postgres()
        
        # 2. Print active limits to act as a spectator
        print("\nVerifying Docker Limits (Spectator View):")
        run_command("docker stats --no-stream")
        
        # 3. Run Optimization (Tuning)
        print(f"\nRunning optimize.py for {c} CPU / {m} Mem configuration...")
        run_command("python optimize.py", env=env)
        
        print(f"\nSaving tuning parameters to results/tuning_{c}cpu_{m}.json")
        run_command(f"cp tuning_config.json results/tuning_{c}cpu_{m}.json")
        
        # 4. Run Benchmark
        print(f"\nRunning main benchmark for {c} CPU / {m} Mem...")
        run_command("python main.py", env=env)
        
        print(f"\nSuccessfully finished {c} CPU / {m} Mem. Results saved to {env['RESULTS_FILENAME']}.")

    print("\nAll experiments finished successfully!")

if __name__ == "__main__":
    main()

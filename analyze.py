"""
analyze.py

Reads all results/benchmark_*cpu*.csv files and computes:
1. Summaries (mean, median, std) per hardware config, phase, bucket, and approach.
2. Intra-hardware Statistical tests: Comparing approaches on the same hardware.
3. Inter-hardware Statistical tests: Comparing the same approach across different hardware.
"""

import sys
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
    from scipy.stats import wilcoxon, mannwhitneyu
except ImportError:
    sys.exit("Please install dependencies: pip install pandas numpy scipy")

RESULTS_DIR = Path("results")
SUMMARY_FILE = Path("results/summary.csv")
STATS_INTRA_FILE = Path("results/statistical_tests_intra_db.csv")
STATS_INTER_FILE = Path("results/statistical_tests_inter_hw.csv")

csv_files = list(RESULTS_DIR.glob("benchmark_*cpu*.csv"))
if not csv_files:
    sys.exit(f"[ERROR] No benchmark results found in {RESULTS_DIR}.")

dfs = []
for f in csv_files:
    config_name = f.stem.replace("benchmark_", "")
    temp_df = pd.read_csv(f)
    temp_df["hardware_config"] = config_name
    dfs.append(temp_df)

df = pd.concat(dfs, ignore_index=True)

# Data conversions
df["elapsed_seconds"] = pd.to_numeric(df["elapsed_seconds"], errors="coerce")
df["filesize_bytes"] = pd.to_numeric(df["filesize_bytes"], errors="coerce")
df["bucket"] = df["bucket"].fillna("ALL")

print("── Aggregating Data ──")
print(f"Loaded {len(csv_files)} hardware configurations: {df['hardware_config'].unique()}")

# 1. Summary Statistics
summary = df.groupby(["hardware_config", "phase", "bucket", "approach"])["elapsed_seconds"].agg(
    ["mean", "median", "std", "count"]
).round(5)
print("\n── Summary ──")
print(summary)
summary.to_csv(SUMMARY_FILE)
print(f"\nSaved summary to {SUMMARY_FILE}")

# 2. Statistical Tests Helpers
def cohens_d(x, y):
    nx = len(x)
    ny = len(y)
    dof = nx + ny - 2
    if dof <= 0: return np.nan
    pool_var = ((nx - 1)*np.var(x, ddof=1) + (ny - 1)*np.var(y, ddof=1)) / dof
    if pool_var == 0: return np.nan
    return (np.mean(x) - np.mean(y)) / np.sqrt(pool_var)

def compute_stats(data1, data2):
    if len(data1) == 0 or len(data2) == 0:
        return None, None, None
    try:
        _, p_m = mannwhitneyu(data1, data2, alternative='two-sided')
    except:
        p_m = np.nan
    p_w = np.nan
    if len(data1) == len(data2):
        try:
            _, p_w = wilcoxon(data1, data2)
        except:
            pass
    d_val = cohens_d(data1, data2)
    return p_m, p_w, d_val

# 3. Intra-Hardware Comparisons (Compare DBs against each other on the same hardware)
stats_intra = []
for hw in df["hardware_config"].unique():
    hw_df = df[df["hardware_config"] == hw]
    for phase in hw_df["phase"].unique():
        phase_df = hw_df[hw_df["phase"] == phase]
        for bucket in phase_df["bucket"].unique():
            bucket_df = phase_df[phase_df["bucket"] == bucket]
            approaches = bucket_df["approach"].unique()
            
            for i in range(len(approaches)):
                for j in range(i + 1, len(approaches)):
                    app1, app2 = approaches[i], approaches[j]
                    d1 = bucket_df[bucket_df["approach"] == app1]["elapsed_seconds"].values
                    d2 = bucket_df[bucket_df["approach"] == app2]["elapsed_seconds"].values
                    
                    p_m, p_w, d_val = compute_stats(d1, d2)
                    stats_intra.append({
                        "hardware_config": hw,
                        "phase": phase,
                        "bucket": bucket,
                        "approach_1": app1,
                        "approach_2": app2,
                        "mann_whitney_p": p_m if pd.notnull(p_m) else None,
                        "wilcoxon_p": p_w if pd.notnull(p_w) else None,
                        "cohens_d": d_val if pd.notnull(d_val) else None
                    })

if stats_intra:
    pd.DataFrame(stats_intra).to_csv(STATS_INTRA_FILE, index=False)
    print(f"\nSaved Intra-Hardware Database Comparisons to {STATS_INTRA_FILE}")

# 4. Inter-Hardware Comparisons (Compare the same DB across different hardware scalability)
stats_inter = []
for app in df["approach"].unique():
    app_df = df[df["approach"] == app]
    for phase in app_df["phase"].unique():
        phase_df = app_df[app_df["phase"] == phase]
        for bucket in phase_df["bucket"].unique():
            bucket_df = phase_df[phase_df["bucket"] == bucket]
            hws = bucket_df["hardware_config"].unique()
            
            for i in range(len(hws)):
                for j in range(i + 1, len(hws)):
                    hw1, hw2 = hws[i], hws[j]
                    d1 = bucket_df[bucket_df["hardware_config"] == hw1]["elapsed_seconds"].values
                    d2 = bucket_df[bucket_df["hardware_config"] == hw2]["elapsed_seconds"].values
                    
                    p_m, p_w, d_val = compute_stats(d1, d2)
                    stats_inter.append({
                        "approach": app,
                        "phase": phase,
                        "bucket": bucket,
                        "hw_config_1": hw1,
                        "hw_config_2": hw2,
                        "mann_whitney_p": p_m if pd.notnull(p_m) else None,
                        "wilcoxon_p": p_w if pd.notnull(p_w) else None,
                        "cohens_d": d_val if pd.notnull(d_val) else None
                    })

if stats_inter:
    pd.DataFrame(stats_inter).to_csv(STATS_INTER_FILE, index=False)
    print(f"Saved Inter-Hardware Scalability Comparisons to {STATS_INTER_FILE}")

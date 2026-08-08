"""
analyze.py

Reads results/benchmark_results.csv and computes:
1. Summaries (mean, median, std) per phase, bucket, and approach.
2. Statistical tests: Wilcoxon signed-rank (for paired data) and Mann-Whitney U (for independent groups).
3. Effect size: Cohen's d.
"""

import sys
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
    from scipy.stats import wilcoxon, mannwhitneyu
except ImportError:
    sys.exit("Please install dependencies: pip install pandas numpy scipy")

RESULTS_FILE = Path("results/benchmark_results.csv")
SUMMARY_FILE = Path("results/summary.csv")
STATS_FILE = Path("results/statistical_tests.csv")

if not RESULTS_FILE.exists():
    sys.exit(f"[ERROR] {RESULTS_FILE} not found. Run main.py first.")

df = pd.read_csv(RESULTS_FILE)

# Data conversions
df["elapsed_seconds"] = pd.to_numeric(df["elapsed_seconds"], errors="coerce")
df["filesize_bytes"] = pd.to_numeric(df["filesize_bytes"], errors="coerce")

# 1. Summary Statistics
summary = df.groupby(["phase", "bucket", "approach"])["elapsed_seconds"].agg(
    ["mean", "median", "std", "count"]
).round(5)
print("── Summary ──")
print(summary)
summary.to_csv(SUMMARY_FILE)
print(f"\nSaved summary to {SUMMARY_FILE}")

# 2. Statistical Tests (Wilcoxon, Mann-Whitney U, Cohen's d)
def cohens_d(x, y):
    nx = len(x)
    ny = len(y)
    dof = nx + ny - 2
    if dof <= 0: return np.nan
    pool_var = ((nx - 1)*np.var(x, ddof=1) + (ny - 1)*np.var(y, ddof=1)) / dof
    if pool_var == 0: return np.nan
    return (np.mean(x) - np.mean(y)) / np.sqrt(pool_var)

stats_results = []
for phase in df["phase"].unique():
    phase_df = df[df["phase"] == phase]
    for bucket in phase_df["bucket"].unique():
        bucket_df = phase_df[phase_df["bucket"] == bucket]
        approaches = bucket_df["approach"].unique()
        
        for i in range(len(approaches)):
            for j in range(i + 1, len(approaches)):
                app1 = approaches[i]
                app2 = approaches[j]
                
                data1 = bucket_df[bucket_df["approach"] == app1]["elapsed_seconds"].values
                data2 = bucket_df[bucket_df["approach"] == app2]["elapsed_seconds"].values
                
                if len(data1) == 0 or len(data2) == 0:
                    continue
                
                # Mann-Whitney U (Independent)
                try:
                    stat_m, p_m = mannwhitneyu(data1, data2, alternative='two-sided')
                except Exception:
                    p_m = np.nan
                
                # Wilcoxon signed-rank (Paired, requires equal lengths)
                p_w = np.nan
                if len(data1) == len(data2):
                    try:
                        stat_w, p_w = wilcoxon(data1, data2)
                    except Exception:
                        pass
                
                # Cohen's d (Effect Size)
                d_val = cohens_d(data1, data2)
                
                stats_results.append({
                    "phase": phase,
                    "bucket": bucket,
                    "approach_1": app1,
                    "approach_2": app2,
                    "mann_whitney_p": round(p_m, 5) if not np.isnan(p_m) else None,
                    "wilcoxon_p": round(p_w, 5) if not np.isnan(p_w) else None,
                    "cohens_d": round(d_val, 5) if not np.isnan(d_val) else None
                })

if stats_results:
    stats_df = pd.DataFrame(stats_results)
    stats_df.to_csv(STATS_FILE, index=False)
    print(f"\nSaved statistical tests to {STATS_FILE}")
    print("\n── Statistical Tests ──")
    print(stats_df.to_string(index=False))

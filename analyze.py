"""
Metrics computed per (bucket × db × operation):
  - mean latency (seconds)
  - std deviation (seconds)         → spread/consistency
  - median latency                  → less sensitive to outliers than mean
  - throughput MB/s                 → normalizes for file size differences
  - n                               → confirm repetition count is as expected
"""

from pathlib import Path
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas not installed. Run: pip install pandas")

RESULTS_FILE  = Path("results/raw_results.csv")
SUMMARY_FILE  = Path("results/summary.csv")

# Load raw result csv
if not RESULTS_FILE.exists():
    sys.exit(f"[ERROR] {RESULTS_FILE} not found. Run client.py first.")

df = pd.read_csv(RESULTS_FILE)

# Sanity check: warn if any bucket/db/op combo has fewer rows than expected
print("── Row counts per group ──")
counts = df.groupby(["bucket", "db", "operation"]).size().rename("n")
print(counts.to_string())
print()

# ── Derived columns ───────────────────────────────────────────────────────────

df["filesize_mb"]       = df["filesize_bytes"] / 1_000_000
df["throughput_mbs"]    = df["filesize_mb"] / df["elapsed_seconds"]

# ── Summary table ─────────────────────────────────────────────────────────────

summary = (
    df.groupby(["bucket", "db", "operation"])
    .agg(
        mean_latency_s   =("elapsed_seconds",  "mean"),
        median_latency_s =("elapsed_seconds",  "median"),
        std_latency_s    =("elapsed_seconds",  "std"),
        mean_throughput  =("throughput_mbs",   "mean"),
        filesize_mb      =("filesize_mb",       "mean"),
        n                =("elapsed_seconds",  "count"),
    )
    .round(5)
)

print("── Summary ──")
print(summary.to_string())

summary.to_csv(SUMMARY_FILE)
print(f"\nSummary saved → {SUMMARY_FILE}")

# ── Quick comparison: Postgres vs Mongo per bucket/operation ──────────────────

print("\n── Latency ratio (postgres / mongodb) ──")
print("  >1.0 means Postgres is slower for that bucket+operation\n")

pivot = (
    df.groupby(["bucket", "operation", "db"])["elapsed_seconds"]
    .mean()
    .unstack("db")
    .round(5)
)

pivot["ratio_pg_over_mongo"] = (pivot["postgres"] / pivot["mongodb"]).round(3)
print(pivot.to_string())

# ── Outlier check ─────────────────────────────────────────────────────────────

print("\n── Top 10 slowest individual measurements ──")
print(df.nlargest(10, "elapsed_seconds")[
    ["bucket", "stored_name", "db", "operation", "elapsed_seconds", "filesize_mb"]
].to_string(index=False))
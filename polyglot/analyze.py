"""
analyze.py

Reads polyglot_results.csv and outputs:
1. Write comparison: polyglot vs pure_mongo (per bucket)
2. Q1: metadata-only query latency: polyglot_pg vs pure_mongo
3. Q2: end-to-end latency: polyglot vs pure_mongo
"""

from pathlib import Path
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("pip install pandas")

RESULTS_FILE = Path("../results/polyglot_results.csv")
SUMMARY_FILE = Path("../results/polyglot_summary.csv")

if not RESULTS_FILE.exists():
    sys.exit(f"[ERROR] {RESULTS_FILE} not found. Run polyglot_client.py first.")

df = pd.read_csv(RESULTS_FILE)

print(f"Total rows: {len(df)}")
print(f"Approaches: {df['approach'].unique()}")
print(f"Query types: {df['query_type'].unique()}\n")

# ── 1. Write comparison ───────────────────────────────────────────────────────

writes = df[df["phase"] == "write"]

write_summary = (
    writes.groupby(["bucket", "approach"])["elapsed_seconds"]
    .agg(mean="mean", median="median", std="std", n="count")
    .round(5)
)

print("── Write Latency: polyglot vs pure_mongo ──")
print(write_summary.to_string())

# ── 2. Metadata-only query ────────────────────────────────────────────────────

meta_q = df[df["query_type"] == "metadata_only"]

meta_summary = (
    meta_q.groupby("approach")["elapsed_seconds"]
    .agg(mean="mean", median="median", std="std", n="count")
    .round(5)
)

print("\n── Q1: Metadata-only Query Latency ──")
print(meta_summary.to_string())

# Ratio: how much faster is PG vs Mongo for metadata queries
if "polyglot_pg" in meta_summary.index and "pure_mongo" in meta_summary.index:
    ratio = meta_summary.loc["pure_mongo", "mean"] / meta_summary.loc["polyglot_pg", "mean"]
    print(f"\n  PG is {ratio:.2f}x faster than pure Mongo for metadata queries")

# ── 3. End-to-end query ───────────────────────────────────────────────────────

e2e_q = df[df["query_type"] == "end_to_end"]

e2e_summary = (
    e2e_q.groupby("approach")["elapsed_seconds"]
    .agg(mean="mean", median="median", std="std", n="count")
    .round(5)
)

print("\n── Q2: End-to-End Latency (metadata + file fetch) ──")
print(e2e_summary.to_string())

if "polyglot" in e2e_summary.index and "pure_mongo" in e2e_summary.index:
    ratio = e2e_summary.loc["polyglot", "mean"] / e2e_summary.loc["pure_mongo", "mean"]
    winner = "polyglot" if ratio < 1 else "pure_mongo"
    print(f"\n  {winner} wins e2e by {abs(1-ratio)*100:.1f}%")

# ── 4. Save full summary ──────────────────────────────────────────────────────

all_summary = (
    df.groupby(["phase", "query_type", "approach"])["elapsed_seconds"]
    .agg(mean="mean", median="median", std="std", n="count")
    .round(5)
)

all_summary.to_csv(SUMMARY_FILE)
print(f"\nFull summary saved → {SUMMARY_FILE}")
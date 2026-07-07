"""
analyze.py

Reads polyglot_results.csv and outputs:
1. Write comparison: polyglot vs pure_mongo per bucket
2. Query latency: metadata-only lookup, polyglot_pg vs pure_mongo
3. Read latency: metadata lookup + file fetch, polyglot vs pure_mongo
4. Operation result sizes, so generated metadata for unlabelled files is visible
"""

from pathlib import Path
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("pip install pandas")

RESULTS_FILE = Path("../results/polyglot_results.csv")
SUMMARY_FILE = Path("../results/polyglot_summary.csv")

EXPECTED_COLUMNS = {
    "phase", "query_type", "actor", "emotion", "bucket",
    "approach", "elapsed_seconds", "n_files",
}


def summarize(frame, group_cols):
    return (
        frame.groupby(group_cols, dropna=False)
        .agg(
            mean_latency_s=("elapsed_seconds", "mean"),
            median_latency_s=("elapsed_seconds", "median"),
            std_latency_s=("elapsed_seconds", "std"),
            mean_n_files=("n_files", "mean"),
            total_files=("n_files", "sum"),
            n=("elapsed_seconds", "count"),
        )
        .round(5)
    )


def print_ratio(summary, left, right, label):
    if left not in summary.index or right not in summary.index:
        return

    left_mean = summary.loc[left, "mean_latency_s"]
    right_mean = summary.loc[right, "mean_latency_s"]
    if left_mean == 0 or pd.isna(left_mean) or pd.isna(right_mean):
        return

    ratio = right_mean / left_mean
    print(f"\n  {label}: {ratio:.2f}x")


def assert_same_file_counts(frame, phase, left, right):
    check = frame[frame["phase"] == phase]
    pivot = check.pivot_table(
        index=["actor", "emotion"],
        columns="approach",
        values="n_files",
        aggfunc="mean",
    )

    if left not in pivot.columns or right not in pivot.columns:
        sys.exit(f"[ERROR] Cannot validate {phase}: missing {left} or {right}")

    mismatched = pivot[pivot[left] != pivot[right]]
    if not mismatched.empty:
        print(f"\n[ERROR] Unfair {phase} comparison: n_files mismatch")
        print(mismatched[[left, right]].to_string())
        sys.exit(
            "Regenerate results with polyglot/client.py so Postgres and Mongo "
            "read/query the same number of files."
        )


if not RESULTS_FILE.exists():
    sys.exit(f"[ERROR] {RESULTS_FILE} not found. Run polyglot/client.py first.")

df = pd.read_csv(RESULTS_FILE)
missing = EXPECTED_COLUMNS - set(df.columns)
if missing:
    sys.exit(f"[ERROR] Missing columns in {RESULTS_FILE}: {sorted(missing)}")

df["elapsed_seconds"] = pd.to_numeric(df["elapsed_seconds"], errors="coerce")
df["n_files"] = pd.to_numeric(df["n_files"], errors="coerce")
df.loc[df["query_type"] == "metadata_only", "phase"] = "query"
df.loc[df["query_type"] == "end_to_end", "phase"] = "read"

assert_same_file_counts(df, "query", "polyglot_pg", "pure_mongo")
assert_same_file_counts(df, "read", "polyglot", "pure_mongo")

print(f"Total rows: {len(df)}")
print(f"Approaches: {df['approach'].unique()}")
print(f"Phases: {df['phase'].unique()}")
print(f"Operation types: {df['query_type'].unique()}")
print(f"Buckets: {df['bucket'].dropna().unique()}\n")

# ── 1. Write comparison ───────────────────────────────────────────────────────

writes = df[df["phase"] == "write"]
write_counts = (
    writes.groupby(["bucket", "approach"], dropna=False)
    .size()
    .rename("rows")
)
write_summary = summarize(writes, ["bucket", "approach"])

print("── Write Latency: polyglot vs pure_mongo ──")
print("\nRows per bucket/approach:")
print(write_counts.to_string())
print()
print(write_summary.to_string())

# ── 2. Query latency ──────────────────────────────────────────────────────────

queries = df[df["phase"] == "query"]
query_summary = summarize(queries, ["approach"])

print("\n── Query Latency: Metadata-only Lookup ──")
print(query_summary.to_string())
print_ratio(query_summary, "polyglot_pg", "pure_mongo", "pure_mongo / polyglot_pg latency")

# ── 3. Read latency ───────────────────────────────────────────────────────────

reads = df[df["phase"] == "read"]
read_summary = summarize(reads, ["approach"])

print("\n── Read Latency: Metadata Lookup + File Fetch ──")
print(read_summary.to_string())

if "polyglot" in read_summary.index and "pure_mongo" in read_summary.index:
    ratio = (
        read_summary.loc["polyglot", "mean_latency_s"]
        / read_summary.loc["pure_mongo", "mean_latency_s"]
    )
    winner = "polyglot" if ratio < 1 else "pure_mongo"
    print(f"\n  {winner} wins read by {abs(1-ratio)*100:.1f}%")

# ── 4. Operation result sizes ─────────────────────────────────────────────────

result_sizes = summarize(
    df[df["phase"].isin(["query", "read"])],
    ["phase", "actor", "emotion", "approach"],
)

print("\n── Operation Result Sizes by Actor/Emotion ──")
print(result_sizes.to_string())

# ── 5. Save full summary ──────────────────────────────────────────────────────

summary_df = df.copy()
summary_df["bucket"] = summary_df["bucket"].fillna("all")
all_summary = summarize(summary_df, ["phase", "query_type", "bucket", "approach"])

all_summary.to_csv(SUMMARY_FILE)
print(f"\nFull summary saved -> {SUMMARY_FILE}")

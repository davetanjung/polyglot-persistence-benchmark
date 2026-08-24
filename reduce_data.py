import os
from pathlib import Path

def reduce_bucket(bucket_path, target=500):
    bucket = Path(bucket_path)
    if not bucket.exists():
        return
        
    files = [f for f in bucket.iterdir() if f.is_file() and f.name != ".DS_Store"]
    
    # Sort files by modification time (oldest first) to ensure the original 10 files are never deleted
    files.sort(key=lambda x: x.stat().st_mtime)
    
    if len(files) <= target:
        print(f"{bucket_path} has {len(files)} files, no need to reduce.")
        return
        
    to_delete = files[target:]
    for f in to_delete:
        f.unlink()
        
    print(f"Deleted {len(to_delete)} files from {bucket_path}. Remaining: {target}")

def main():
    buckets = [
        "data/audio/300-400kb",
        "data/audio/500-600kb",
        "data/video/5-6mb",
        "data/video/10-17mb"
    ]
    for b in buckets:
        reduce_bucket(b, target=500)

if __name__ == "__main__":
    main()

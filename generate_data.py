import os
import shutil
import random
from pathlib import Path

def generate_ravdess_names(modality_choices, channel_choices, ext):
    emotions = [f"{i:02d}" for i in range(1, 9)]
    intensities = ["01", "02"]
    statements = ["01", "02"]
    repetitions = ["01", "02"]
    actors = [f"{i:02d}" for i in range(1, 25)]
    
    for m in modality_choices:
        for c in channel_choices:
            for e in emotions:
                for i in intensities:
                    for s in statements:
                        for r in repetitions:
                            for a in actors:
                                yield f"{m}-{c}-{e}-{i}-{s}-{r}-{a}{ext}"

def tsinghua_generator():
    i = 1
    while True:
        yield f"tsinghua_v_{i:04d}.mp4"
        i += 1

def pad_bucket(bucket_path, generator, target=1000):
    bucket = Path(bucket_path)
    if not bucket.exists():
        print(f"Bucket {bucket_path} not found.")
        return
        
    existing_files = [f for f in bucket.iterdir() if f.is_file() and f.name != ".DS_Store"]
    original_files = list(existing_files)
    existing_names = {f.name for f in existing_files}
    
    if len(existing_files) >= target:
        print(f"Bucket {bucket_path} already has {len(existing_files)} files.")
        return
        
    needed = target - len(existing_files)
    print(f"Generating {needed} files for {bucket_path}...")
    
    gen = generator()
    count = 0
    for new_name in gen:
        if count >= needed:
            break
        if new_name in existing_names:
            continue
            
        src = random.choice(original_files)
        dst = bucket / new_name
        shutil.copy2(src, dst)
        existing_names.add(new_name)
        count += 1
        
        if count % 100 == 0:
            print(f"  ... {count}/{needed} generated")
    
    print(f"Finished {bucket_path}: Total {len(existing_names)} files.")

def main():
    # Audio small (300-400kb): 03, 01, .wav
    pad_bucket("data/audio/300-400kb", lambda: generate_ravdess_names(["03"], ["01"], ".wav"))
    
    # Audio medium (500-600kb): 03, 02, .wav
    pad_bucket("data/audio/500-600kb", lambda: generate_ravdess_names(["03"], ["02"], ".wav"))
    
    # Video small (5-6mb): 01, 02, .mp4 (using 01 and 02 for modality)
    pad_bucket("data/video/5-6mb", lambda: generate_ravdess_names(["01", "02"], ["01", "02"], ".mp4"))
    
    # Video medium (10-17mb): Tsinghua
    pad_bucket("data/video/10-17mb", tsinghua_generator)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Inspect mat files to determine their contents and plan the training pipeline.

Run this on AutoDL:
    python scripts/11_inspect_datasets.py

This tells us exactly what's in each mat file (keys, shapes, dtypes)
so we can design the correct data pipeline.
"""
import os
import sys
from pathlib import Path

def inspect_mat(path: str):
    """Load a .mat file and print all keys with shapes."""
    try:
        import scipy.io as sio
        data = sio.loadmat(path)
    except NotImplementedError:
        # HDF5-based .mat v7.3 files need h5py
        import h5py
        print(f"  [v7.3 HDF5 format, using h5py]")
        with h5py.File(path, "r") as f:
            for key in f.keys():
                obj = f[key]
                if hasattr(obj, "shape"):
                    print(f"  {key:30s} shape={obj.shape}  dtype={obj.dtype}")
                else:
                    print(f"  {key:30s} type={type(obj)}")
        return

    for key, val in data.items():
        if key.startswith("__"):
            continue
        if hasattr(val, "shape"):
            print(f"  {key:30s} shape={val.shape}  dtype={val.dtype}")
            # Show a small sample for feature arrays
            if val.ndim == 2 and val.shape[0] > 5 and val.shape[1] > 1:
                print(f"    min={val.min():.4f}  max={val.max():.4f}  mean={val.mean():.4f}")
                # Check if it looks like binary labels (0/1)
                unique_vals = set(val.flatten()[:1000].tolist())
                if unique_vals <= {0.0, 1.0, 0, 1}:
                    print(f"    → looks like BINARY LABELS")
                elif val.max() <= 1.0 and val.min() >= -1.0:
                    print(f"    → looks like NORMALIZED FEATURES")
                else:
                    print(f"    → looks like RAW FEATURES")
        else:
            print(f"  {key:30s} type={type(val)}")


def inspect_pth(path: str):
    """Load a .pth/.pt file and print all keys with shapes."""
    import torch
    ckpt = torch.load(path, map_location="cpu", weights_only=False)

    if isinstance(ckpt, dict):
        # Could be a state_dict or a dict containing state_dict
        if "state_dict" in ckpt:
            print("  [has 'state_dict' key — extracting]")
            for k, v in ckpt.items():
                if k != "state_dict":
                    print(f"  META  {k:40s} = {v}" if not hasattr(v, "shape")
                          else f"  META  {k:40s} shape={v.shape}")
            sd = ckpt["state_dict"]
        elif any(k.endswith(".weight") or k.endswith(".bias") for k in ckpt.keys()):
            sd = ckpt
        else:
            # Print top-level keys
            for k, v in ckpt.items():
                if hasattr(v, "shape"):
                    print(f"  {k:50s} shape={tuple(v.shape)}  dtype={v.dtype}")
                elif isinstance(v, dict) and len(v) < 5:
                    print(f"  {k:50s} dict with {len(v)} keys")
                else:
                    print(f"  {k:50s} type={type(v).__name__}")
            return

        print(f"  --- state_dict: {len(sd)} parameters ---")
        for k, v in sd.items():
            print(f"  {k:50s} shape={tuple(v.shape)}  dtype={v.dtype}")
    else:
        print(f"  type={type(ckpt)}, not a dict")


def scan_directory(root: str, extensions=(".mat", ".pth", ".pt")):
    """Recursively find all files with given extensions."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        for f in sorted(filenames):
            if any(f.endswith(ext) for ext in extensions):
                results.append(os.path.join(dirpath, f))
    return results


def main():
    # Paths to scan
    paths_to_scan = [
        "/root/autodl-tmp/dataset",
        "/root/autodl-tmp/database",
        "/root/autodl-tmp/PAMIH",       # in case PAMIH code is here
    ]

    # Also check for PAMIH checkpoints
    pamih_ckpt_paths = [
        "/root/autodl-tmp/PAMIH/checkpoints",
        "/root/autodl-tmp/PAMIH/output",
        "/root/autodl-tmp/PAMIH",
    ]

    print("=" * 70)
    print("STEP 1: Scanning for .mat / .pth / .pt files")
    print("=" * 70)

    all_files = []
    for root in paths_to_scan:
        if os.path.exists(root):
            files = scan_directory(root)
            all_files.extend(files)
            if files:
                print(f"\n{root}:")
                for f in files:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    print(f"  {f}  ({size_mb:.1f} MB)")
            else:
                print(f"\n{root}: (no .mat/.pth/.pt files)")
        else:
            print(f"\n{root}: (directory does not exist)")

    if not all_files:
        print("\nNo .mat/.pth/.pt files found! Check your paths.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("STEP 2: Inspecting each file")
    print("=" * 70)

    for filepath in all_files:
        print(f"\n{'─' * 60}")
        print(f"FILE: {filepath}")
        print(f"SIZE: {os.path.getsize(filepath) / (1024*1024):.1f} MB")
        print(f"{'─' * 60}")

        if filepath.endswith(".mat"):
            try:
                inspect_mat(filepath)
            except Exception as e:
                print(f"  ERROR: {e}")
        elif filepath.endswith((".pth", ".pt")):
            try:
                inspect_pth(filepath)
            except Exception as e:
                print(f"  ERROR: {e}")

    # Also check for raw image directories
    print("\n" + "=" * 70)
    print("STEP 3: Checking for raw image/text data")
    print("=" * 70)

    raw_dirs = [
        "/root/autodl-tmp/database/coco",
        "/root/autodl-tmp/database/nuswide",
        "/root/autodl-tmp/database/flickr",
        "/root/autodl-tmp/database/flickr25k",
        "/root/autodl-tmp/database/iapr",
        "/root/autodl-tmp/database/iaprtc12",
    ]
    for d in raw_dirs:
        if os.path.exists(d):
            # Count files
            n_files = sum(len(files) for _, _, files in os.walk(d))
            # Check for images
            n_images = sum(
                1 for _, _, files in os.walk(d)
                for f in files
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))
            )
            print(f"  {d}: {n_files} files total, {n_images} images")

            # Show first few files
            for _, _, files in os.walk(d):
                for f in sorted(files)[:5]:
                    print(f"    sample: {f}")
                break
        else:
            print(f"  {d}: NOT FOUND")

    # Check for COCO annotations
    anno_paths = [
        "/root/autodl-tmp/database/coco/annotations",
        "/root/autodl-tmp/database/annotations",
    ]
    for d in anno_paths:
        if os.path.exists(d):
            files = os.listdir(d)
            print(f"\n  Annotations at {d}:")
            for f in sorted(files):
                size_mb = os.path.getsize(os.path.join(d, f)) / (1024*1024)
                print(f"    {f}  ({size_mb:.1f} MB)")

    # NUS-WIDE caption txt files
    nuswide_txt_dirs = [
        "/root/autodl-tmp/database/nuswide",
        "/root/autodl-tmp/dataset/nuswide",
    ]
    for d in nuswide_txt_dirs:
        if os.path.exists(d):
            txt_files = [f for f in os.listdir(d) if f.endswith(".txt")]
            if txt_files:
                print(f"\n  NUS-WIDE text files at {d}:")
                for f in sorted(txt_files)[:5]:
                    print(f"    {f}")

    print("\n" + "=" * 70)
    print("STEP 4: DIAGNOSIS & RECOMMENDED PATH")
    print("=" * 70)
    print("""
Based on the inspection above, one of these scenarios applies:

SCENARIO A: mat files contain CLIP features (512-d image, 512-d text)
  → These CANNOT directly train HashMM-RAG hash heads (needs 1024-d/768-d)
  → But the LABEL MATRIX is usable for supervision
  → Need to re-encode with BGE-M3/SigLIP-2 if raw data exists

SCENARIO B: mat files contain CLIP features + raw images exist in /database/
  → BEST CASE: use raw images with SigLIP-2, raw captions with BGE-M3
  → Use labels from mat for supervision
  → Encode once, cache as .npz, train hash heads on 10k+ pairs

SCENARIO C: mat files contain features from OTHER encoders (VGG, ResNet, BoW)
  → Same as Scenario A: features incompatible with HashMM-RAG
  → Labels still usable

ACTION: Copy-paste the output of this script and send it to me.
I will then write the exact training pipeline based on what you have.
""")


if __name__ == "__main__":
    main()

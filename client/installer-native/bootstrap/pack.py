#!/usr/bin/env python3
# -*- coding: ascii -*-
"""pack.py - assemble the single self-extracting HashMM-Setup.exe (V102).

Replaces the old bootstrap/pack.ps1, which appended the payload RAW (zero
compression) -> a 826 MB exe. This packer DEFLATE-compresses the whole
payload/ folder into one .zip blob and appends it to the bootstrap stub, so
the shipped exe is a fraction of the size. It also runs on ANY OS (Linux/CI
included), unlike the PowerShell version -- so packaging no longer requires a
Windows box for this step.

Exe layout produced:
    [ bootstrap stub bytes ........... ]   <- bootstrap.exe (tiny Win32)
    [ zip(payload/) bytes ............ ]   <- one DEFLATE .zip of the whole tree
    [ footer: <q zipOffset><8s MAGIC> ]   <- 16 bytes, little-endian

bootstrap.c reads the 16-byte footer, slices the .zip back out to %TEMP%,
extracts it with the OS-native unzip (tar.exe, Win10 1803+; PowerShell
Expand-Archive fallback), then launches the extracted Qt installer.

Codec note: zipfile.ZIP_DEFLATED is chosen because Windows' built-in tar.exe
and Expand-Archive both read it with NO third-party dependency. The ratio is
modest on already-compressed binaries (Chromium .dll/.pak); the big size win
comes from NOT bundling the Python runtime / onnxruntime in the first place
(see desktop/electron-builder.yml changes). If you later want LZMA-class
ratio, ship a 0.5 MB 7zr.exe next to the stub and switch build_archive() to
emit .7z -- the footer/stub contract stays identical.

Usage:
    python pack.py --folder payload --stub bootstrap/bootstrap.exe \
                   --out HashMM-Setup.exe
"""
import argparse
import io
import os
import struct
import sys
import zipfile

# 8 bytes exactly. Distinct from the old raw format's "HMSFX1\0\0" so a new
# stub never misreads an old exe (and vice versa).
MAGIC = b"HMSFXZ1\x00"


def build_archive(folder):
    """DEFLATE-zip the whole folder tree into an in-memory blob.

    Stored paths are forward-slash relative (zip convention); tar.exe and
    Expand-Archive both recreate the tree correctly from these.
    """
    folder = os.path.abspath(folder)
    buf = io.BytesIO()
    # compresslevel kwarg exists on Python 3.7+; HashMM packaging uses >=3.10.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, folder).replace(os.sep, "/")
                z.write(full, rel)
    return buf.getvalue()


def folder_raw_size(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def main():
    ap = argparse.ArgumentParser(description="Pack payload + stub -> single exe.")
    ap.add_argument("--folder", required=True, help="payload folder to embed")
    ap.add_argument("--stub", required=True, help="bootstrap.exe stub")
    ap.add_argument("--out", required=True, help="output self-extracting exe")
    a = ap.parse_args()

    if not os.path.isdir(a.folder):
        print("[pack] ERROR: payload folder not found: %s" % a.folder, file=sys.stderr)
        return 1
    if not os.path.isfile(a.stub):
        print("[pack] ERROR: stub not found: %s" % a.stub, file=sys.stderr)
        return 1

    with open(a.stub, "rb") as f:
        stub = f.read()
    zip_bytes = build_archive(a.folder)

    with open(a.out, "wb") as f:
        f.write(stub)
        zip_offset = len(stub)
        f.write(zip_bytes)
        f.write(struct.pack("<q", zip_offset))   # i64 LE: where the zip starts
        f.write(MAGIC)                            # 8-byte sentinel

    raw = folder_raw_size(a.folder)
    out_sz = os.path.getsize(a.out)
    pct = (100.0 * out_sz / raw) if raw else 0.0
    print("[pack] payload raw %.1f MB  ->  exe %.1f MB  (%.0f%% of raw; "
          "zip blob %.1f MB)" % (raw / 1048576.0, out_sz / 1048576.0, pct,
                                 len(zip_bytes) / 1048576.0))
    print("[pack] wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

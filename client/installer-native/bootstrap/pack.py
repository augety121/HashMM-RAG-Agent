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
import datetime
import io
import os
import shutil
import struct
import sys
import tempfile
import zipfile

# 8 bytes exactly. Distinct from the old raw format's "HMSFX1\0\0" so a new
# stub never misreads an old exe (and vice versa).
MAGIC = b"HMSFXZ1\x00"


def _files(folder):
    folder = os.path.abspath(folder)
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs.sort()
        files.sort()
        for name in list(dirs) + list(files):
            full = os.path.join(root, name)
            if os.path.islink(full):
                raise RuntimeError("payload must not contain symlinks: %s" % full)
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            if rel.startswith("../") or rel.startswith("/") or "/../" in "/" + rel:
                raise RuntimeError("unsafe payload path: %s" % rel)
            yield full, rel


def _zip_timestamp():
    # Reproducible by default. SOURCE_DATE_EPOCH may opt into a release timestamp.
    raw = os.environ.get("SOURCE_DATE_EPOCH", "")
    try:
        dt = datetime.datetime.utcfromtimestamp(int(raw)) if raw else datetime.datetime(1980, 1, 1)
    except (ValueError, OverflowError, OSError):
        dt = datetime.datetime(1980, 1, 1)
    if dt.year < 1980:
        dt = datetime.datetime(1980, 1, 1)
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _write_archive(folder, target):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=True) as archive:
        for full, rel in _files(folder):
            info = zipfile.ZipInfo(rel, _zip_timestamp())
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with open(full, "rb") as source, archive.open(info, "w", force_zip64=True) as dest:
                shutil.copyfileobj(source, dest, length=4 * 1024 * 1024)


def build_archive(folder):
    """Compatibility helper used by tests; release builds use the streaming path."""
    buf = io.BytesIO()
    _write_archive(folder, buf)
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

    out_dir = os.path.dirname(os.path.abspath(a.out)) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    zip_tmp = tempfile.NamedTemporaryFile(prefix="hashmm-payload-", suffix=".zip",
                                          dir=out_dir, delete=False)
    zip_tmp.close()
    out_tmp = tempfile.NamedTemporaryFile(prefix="hashmm-setup-", suffix=".tmp",
                                          dir=out_dir, delete=False)
    out_tmp.close()
    try:
        _write_archive(a.folder, zip_tmp.name)
        with open(a.stub, "rb") as stub_file, open(zip_tmp.name, "rb") as archive, open(out_tmp.name, "wb") as out:
            shutil.copyfileobj(stub_file, out, length=4 * 1024 * 1024)
            zip_offset = out.tell()
            shutil.copyfileobj(archive, out, length=4 * 1024 * 1024)
            out.write(struct.pack("<q", zip_offset))
            out.write(MAGIC)
            out.flush()
            os.fsync(out.fileno())
        os.replace(out_tmp.name, a.out)
    finally:
        for temp_path in (zip_tmp.name, out_tmp.name):
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass

    raw = folder_raw_size(a.folder)
    out_sz = os.path.getsize(a.out)
    pct = (100.0 * out_sz / raw) if raw else 0.0
    print("[pack] payload raw %.1f MB  ->  exe %.1f MB  (%.0f%% of raw; "
          "zip blob %.1f MB)" % (raw / 1048576.0, out_sz / 1048576.0, pct,
                                 (out_sz - zip_offset - 16) / 1048576.0))
    print("[pack] wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: ascii -*-
"""prepare-runtime.py -- build the bundled Python runtime (V89).

HashMM's answer to Marvis' MarvisNode.exe: ship a self-contained Python
(python.org "embeddable" build) with ALL backend deps pre-installed inside
the installer, so end users need ZERO environment setup -- no Python, no
pip, no venv. They just click "start".

Run this on the *packaging* machine (Windows, where you run `npm run
dist:win`). It is wired into the `predist:win` npm hook and is idempotent:
if desktop/runtime/ already matches the current requirements hash it exits
immediately.

Pipeline:
  1. download python-<ver>-embed-amd64.zip  ->  desktop/runtime/python/
  2. enable site-packages (edit python3XX._pth: uncomment `import site`,
     append `Lib\\site-packages`)
  3. download get-pip.py and bootstrap pip into the embedded runtime
  4. sanitize requirements.txt (strip comments -> ASCII runtime list,
     same logic as desktop/backendmgr.js) and `pip install` everything
  5. slim down (__pycache__) and write runtime-info.json (version + hash)

Graceful degradation: ANY failure leaves desktop/runtime/ in place with a
README and exits 0 (with a loud warning), so `npm run dist:win` still
produces an installer -- it just falls back to the V88 venv flow at the
user side. Use --strict to make failures fatal instead.

Options:
  --py-version 3.12.8      embeddable version to bundle
  --py-mirror URL          base URL for the embed zip (default python.org;
                           cn mirror e.g. https://mirrors.huaweicloud.com/python)
  --mirror URL             pip index mirror for dependency install
  --force                  rebuild even if hash matches
  --download-only          stop after step 2 (used for sandbox self-test
                           on non-Windows; never installs pip/deps)
  --strict                 exit non-zero on failure (CI)
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
REPO = os.path.dirname(DESKTOP)
RUNTIME = os.path.join(DESKTOP, "runtime")
PYDIR = os.path.join(RUNTIME, "python")
INFO = os.path.join(RUNTIME, "runtime-info.json")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def log(msg):
    print("[runtime] %s" % msg, flush=True)


def sanitize_requirements(src):
    """Mirror of backendmgr.sanitizeRequirements: comments/blank/non-ASCII out."""
    keep = []
    with io.open(src, "r", encoding="utf-8") as f:
        for raw in f.read().splitlines():
            line = raw.split("#")[0].strip()
            if not line:
                continue
            try:
                line.encode("ascii")
            except UnicodeEncodeError:
                log("skip non-ascii line: %r" % raw[:60])
                continue
            keep.append(line)
    return keep


def req_hash(lines):
    return hashlib.sha1("\n".join(lines).encode("ascii")).hexdigest()[:12]


def download(url, dest):
    log("download %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "hashmm-runtime/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    log("  -> %s (%.1f MB)" % (dest, os.path.getsize(dest) / 1048576.0))


def enable_site(pydir):
    """Embeddable ships python3XX._pth with `#import site`; flip it on and
    add Lib\\site-packages so pip-installed packages resolve."""
    pth = None
    for name in os.listdir(pydir):
        if name.startswith("python") and name.endswith("._pth"):
            pth = os.path.join(pydir, name)
            break
    if not pth:
        raise RuntimeError("could not find python3XX._pth in %s" % pydir)
    with io.open(pth, "r", encoding="ascii", errors="replace") as f:
        lines = [l.rstrip("\r\n") for l in f]
    out = []
    for l in lines:
        out.append("import site" if l.strip() == "#import site" else l)
    if "Lib\\site-packages" not in out:
        out.insert(-1 if out and out[-1] == "import site" else len(out), "Lib\\site-packages")
    with io.open(pth, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.makedirs(os.path.join(pydir, "Lib", "site-packages"), exist_ok=True)
    log("site enabled in %s" % os.path.basename(pth))
    return pth


def write_fallback_readme(reason):
    os.makedirs(RUNTIME, exist_ok=True)
    with io.open(os.path.join(RUNTIME, "README.txt"), "w", encoding="utf-8") as f:
        f.write(
            "HashMM bundled runtime was NOT built on the packaging machine.\n"
            "Reason: %s\n\n"
            "The installer still works: the app falls back to detecting a\n"
            "system Python and creating a venv (V88 flow). To ship the\n"
            "zero-setup runtime, run on Windows:\n"
            "    python desktop/scripts/prepare-runtime.py\n"
            "then `npm run dist:win` again.\n" % reason)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py-version", default="3.12.8")
    ap.add_argument("--py-mirror", default="https://www.python.org/ftp/python")
    ap.add_argument("--mirror", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    try:
        run(a)
        return 0
    except Exception as e:  # graceful: never block `npm run dist:win`
        log("FAILED: %s" % e)
        write_fallback_readme(str(e))
        if a.strict:
            return 1
        log("!" * 64)
        log("WARNING: installer will NOT contain the bundled runtime;")
        log("users fall back to the system-Python/venv flow. See runtime/README.txt")
        log("!" * 64)
        return 0


def run(a):
    req_lines = sanitize_requirements(os.path.join(REPO, "requirements.txt"))
    h = req_hash(req_lines)
    log("requirements: %d packages, hash=%s" % (len(req_lines), h))

    # idempotency
    if not a.force and os.path.exists(INFO):
        try:
            with io.open(INFO, "r", encoding="utf-8") as f:
                old = json.load(f)
            if old.get("req_hash") == h and old.get("complete"):
                log("runtime up-to-date (hash match) -- skip. Use --force to rebuild.")
                return
        except Exception:
            pass

    if os.name != "nt" and not a.download_only:
        raise RuntimeError("bundled runtime targets Windows; run this on the "
                           "Windows packaging machine (or use --download-only to test)")

    # fresh dir
    if os.path.exists(PYDIR):
        shutil.rmtree(PYDIR)
    os.makedirs(PYDIR, exist_ok=True)

    # 1. embeddable zip
    ver = a.py_version
    url = "%s/%s/python-%s-embed-amd64.zip" % (a.py_mirror.rstrip("/"), ver, ver)
    zpath = os.path.join(RUNTIME, "python-embed.zip")
    download(url, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(PYDIR)
    os.remove(zpath)
    log("extracted %d entries -> %s" % (len(os.listdir(PYDIR)), PYDIR))

    # 2. enable site
    enable_site(PYDIR)

    if a.download_only:
        log("--download-only: stopping before pip (layout verified)")
        with io.open(INFO, "w", encoding="utf-8") as f:
            json.dump({"py_version": ver, "req_hash": h, "complete": False,
                       "download_only": True}, f, indent=2)
        return

    pyexe = os.path.join(PYDIR, "python.exe")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PIP_NO_INPUT="1")

    # 3. pip bootstrap
    gp = os.path.join(RUNTIME, "get-pip.py")
    download(GET_PIP_URL, gp)
    subprocess.run([pyexe, gp, "--no-warn-script-location"], check=True, env=env)
    os.remove(gp)

    # 4. install deps into the embedded runtime
    rt_req = os.path.join(RUNTIME, "requirements.runtime.txt")
    with io.open(rt_req, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(req_lines) + "\n")
    pip_args = [pyexe, "-m", "pip", "install", "-r", rt_req,
                "--disable-pip-version-check", "--prefer-binary",
                "--no-warn-script-location"]
    if a.mirror:
        pip_args += ["-i", a.mirror]
    subprocess.run(pip_args, check=True, env=env)

    # verify, same bar as backendmgr
    subprocess.run([pyexe, "-c", "import fastapi, uvicorn, numpy, multipart"],
                   check=True, env=env)

    # 5. slim + stamp
    removed = 0
    for root, dirs, _files in os.walk(PYDIR):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)
                removed += 1
    size = 0
    for root, _d, files in os.walk(PYDIR):
        for fn in files:
            try:
                size += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    with io.open(INFO, "w", encoding="utf-8") as f:
        json.dump({"py_version": ver, "req_hash": h, "complete": True,
                   "packages": len(req_lines), "size_mb": round(size / 1048576.0, 1)},
                  f, indent=2)
    log("DONE: bundled runtime ready (%.0f MB, %d pycache pruned)" % (size / 1048576.0, removed))
    log("next: npm run dist:win  (runtime ships inside resources/runtime)")


if __name__ == "__main__":
    sys.exit(main())

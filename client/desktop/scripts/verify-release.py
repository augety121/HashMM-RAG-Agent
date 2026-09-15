#!/usr/bin/env python3
"""Strict source and packaged-desktop verification for installer-native."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
NATIVE = ROOT / "installer-native"
RELEASE_MANIFEST = ROOT / "hashmm" / "release-manifest.json"


def fail(message: str) -> None:
    raise RuntimeError(message)


def requirement_hash(path: Path) -> str:
    keep: list[str] = []
    for raw in path.read_text("utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        line.encode("ascii")
        keep.append(line)
    return hashlib.sha1("\n".join(keep).encode("ascii")).hexdigest()[:12]


def release_data(path: Path = RELEASE_MANIFEST) -> dict:
    if not path.is_file():
        fail(f"release manifest missing: {path}")
    data = json.loads(path.read_text("utf-8"))
    required = {
        "schema", "product_version", "release", "backend_version", "api_version",
        "desktop_version", "android_version_name", "android_version_code", "protocols",
        "minimum_compatible", "artifact_policy",
    }
    missing = sorted(required - set(data))
    if data.get("schema") != "hashmm.release-manifest.v1" or missing:
        fail(f"release manifest invalid; missing={missing}")
    return data


def backend_release(init_file: Path) -> str:
    return str(release_data(init_file.with_name("release-manifest.json"))["release"])


def versions() -> tuple[str, str]:
    manifest = release_data()
    desktop_version = json.loads((DESKTOP / "package.json").read_text("utf-8"))["version"]
    frontend_version = json.loads((ROOT / "frontend-next" / "package.json").read_text("utf-8"))["version"]
    cmake = (NATIVE / "CMakeLists.txt").read_text("utf-8")
    match = re.search(r"project\(HashMMSetup VERSION ([0-9.]+)", cmake)
    if not match:
        fail("installer-native CMake project version missing")
    native_version = match.group(1)
    expected_desktop = str(manifest["desktop_version"])
    if len({desktop_version, frontend_version, native_version, expected_desktop}) != 1:
        fail(
            "release manifest/desktop/frontend/native mismatch: "
            f"{expected_desktop} / {desktop_version} / {frontend_version} / {native_version}"
        )
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    if f'version = "{manifest["backend_version"]}"' not in pyproject:
        fail("pyproject backend version differs from release manifest")
    for relative in ("mcp/hashmm-mcp-server.js", "mcp/mcp-host.js"):
        text = (DESKTOP / relative).read_text("utf-8")
        if f'version: "{desktop_version}"' not in text:
            fail(f"desktop protocol version stale in {relative}; expected {desktop_version}")
    android_gradle = ROOT.parent / "app" / "app" / "build.gradle.kts"
    if android_gradle.is_file():
        android_text = android_gradle.read_text("utf-8")
        android_name = re.search(r'versionName\s*=\s*"([^"]+)"', android_text)
        android_code = re.search(r"versionCode\s*=\s*(\d+)", android_text)
        if not android_name or not android_code:
            fail("Android version declarations missing")
        expected_name = str(manifest["android_version_name"])
        expected_code = int(manifest["android_version_code"])
        if android_name.group(1) != expected_name or int(android_code.group(1)) != expected_code:
            fail(
                "release manifest/Android mismatch: "
                f"{expected_name}/{expected_code} != {android_name.group(1)}/{android_code.group(1)}"
            )
    return desktop_version, str(manifest["release"])


def verify_runtime(runtime: Path, requirements: Path) -> None:
    info_file = runtime / "runtime-info.json"
    python = runtime / "python" / "python.exe"
    if not info_file.is_file() or not python.is_file():
        fail(f"bundled runtime incomplete: {runtime}")
    info = json.loads(info_file.read_text("utf-8"))
    if info.get("complete") is not True:
        fail("runtime-info.json does not declare complete=true")
    expected = requirement_hash(requirements)
    if info.get("req_hash") != expected:
        fail(f"runtime dependency hash stale: {info.get('req_hash')} != {expected}")
    result = subprocess.run(
        [str(python), "-c",
         "import cryptography,fastapi,multipart,numpy,uvicorn,yaml; print('runtime-import-ok')"],
        text=True, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        fail("bundled runtime import check failed: " + (result.stderr or result.stdout).strip()[-500:])


def verify_source() -> tuple[str, str]:
    version, release = versions()
    webui = ROOT / "frontend-next" / "out" / "index.html"
    if not webui.is_file() or webui.stat().st_size < 1000:
        fail("frontend-next/out/index.html missing or implausibly small; run npm run build")
    verify_runtime(DESKTOP / "runtime", ROOT / "requirements.txt")
    print(f"[release] source OK: desktop={version} backend={release} runtime+webui verified")
    return version, release


def verify_packaged(app_dir: Path) -> None:
    version, release = verify_source()
    app_dir = app_dir.resolve()
    resources = app_dir / "resources"
    required = [
        app_dir / "HashMM.exe",
        resources / "app.asar",
        resources / "webui" / "index.html",
        resources / "backend" / "requirements.txt",
        resources / "backend" / "hashmm" / "__init__.py",
        resources / "backend" / "hashmm" / "release-manifest.json",
        resources / "runtime" / "runtime-info.json",
        resources / "runtime" / "python" / "python.exe",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        fail("packaged app missing:\n  " + "\n  ".join(missing))
    packaged_release = backend_release(resources / "backend" / "hashmm" / "__init__.py")
    if packaged_release != release:
        fail(f"packaged backend stale: {packaged_release} != {release}")
    verify_runtime(resources / "runtime", resources / "backend" / "requirements.txt")
    py = resources / "runtime" / "python" / "python.exe"
    backend = resources / "backend"
    code = (
        "import pathlib,sys; sys.dont_write_bytecode=True; root=pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0,str(root)); "
        "import hashmm; actual=pathlib.Path(hashmm.__file__).resolve(); "
        "assert root in actual.parents,(root,actual); assert hashmm.RELEASE==sys.argv[2],hashmm.RELEASE; "
        "print(hashmm.RELEASE)"
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([str(py), "-c", code, str(backend), release], text=True,
                            capture_output=True, timeout=30, check=False, env=env)
    if result.returncode:
        fail("packaged backend import failed: " + (result.stderr or result.stdout).strip()[-500:])
    junk = [path for path in (resources / "backend" / "hashmm").rglob("*")
            if path.name == "__pycache__" or path.suffix == ".pyc"]
    if junk:
        fail(f"packaged backend contains build junk: {junk[0]}")
    print(f"[release] packaged app OK: {app_dir} · desktop={version} backend={release}")


def emit_artifact(artifact: Path) -> None:
    version, release = versions()
    source_manifest = release_data()
    artifact = artifact.resolve()
    if not artifact.is_file() or artifact.stat().st_size < 1024 * 1024:
        fail(f"setup artifact missing or too small: {artifact}")
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    hexdigest = digest.hexdigest()
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{hexdigest} *{artifact.name}\n", encoding="ascii", newline="\n")
    manifest = {
        "schema": "hashmm.artifact-release.v2",
        "product": "HashMM",
        "product_version": source_manifest["product_version"],
        "desktop_version": version,
        "backend_version": source_manifest["backend_version"],
        "backend_release": release,
        "api_version": source_manifest["api_version"],
        "protocols": source_manifest["protocols"],
        "minimum_compatible": source_manifest["minimum_compatible"],
        "artifact": artifact.name,
        "bytes": artifact.stat().st_size,
        "sha256": hexdigest,
        "publisher_signature": {
            "required": os.environ.get("HASHMM_REQUIRE_SIGNING") == "1",
            "verified_by_build": bool(os.environ.get("HASHMM_SIGN_PFX")),
        },
    }
    artifact.with_suffix(".release.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[release] artifact manifest OK: sha256={hexdigest}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--packaged", type=Path)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    try:
        if args.packaged:
            verify_packaged(args.packaged)
        elif args.artifact:
            emit_artifact(args.artifact)
        else:
            verify_source()
        return 0
    except Exception as exc:
        print(f"[release] FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Validated single source of truth for HashMM source and release versions."""
from __future__ import annotations

import json
import pkgutil
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RELEASE = re.compile(r"^V[0-9]+$")
_REQUIRED_PROTOCOLS = {"event", "sync", "work", "remote", "provider", "canvas"}


class ReleaseManifestError(RuntimeError):
    """Raised when the shipped source contains an invalid release manifest."""


def manifest_path() -> Path:
    return Path(__file__).with_name("release-manifest.json")


def _read_manifest_text() -> str:
    """Read the manifest from a source tree, installed wheel or server ZIP."""

    try:
        return manifest_path().read_text("utf-8")
    except OSError as path_error:
        try:
            payload = pkgutil.get_data("hashmm", "release-manifest.json")
        except (OSError, ImportError) as resource_error:
            raise ReleaseManifestError("release manifest is missing or unreadable") from resource_error
        if payload is None:
            raise ReleaseManifestError("release manifest is missing or unreadable") from path_error
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseManifestError("release manifest is missing or unreadable") from exc


def _require_text(data: dict[str, Any], key: str, pattern: re.Pattern[str] | None = None) -> str:
    value = str(data.get(key) or "").strip()
    if not value or (pattern is not None and not pattern.fullmatch(value)):
        raise ReleaseManifestError(f"invalid release manifest field: {key}")
    return value


def validate_manifest(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema") != "hashmm.release-manifest.v1":
        raise ReleaseManifestError("unsupported release manifest schema")
    for key in ("product_version", "backend_version", "api_version", "desktop_version", "android_version_name"):
        _require_text(data, key, _SEMVER)
    _require_text(data, "release", _RELEASE)
    code = data.get("android_version_code")
    if not isinstance(code, int) or code < 1:
        raise ReleaseManifestError("invalid release manifest field: android_version_code")
    protocols = data.get("protocols")
    if not isinstance(protocols, dict) or not _REQUIRED_PROTOCOLS.issubset(protocols):
        raise ReleaseManifestError("release manifest protocols are incomplete")
    if any(not str(protocols.get(key) or "").startswith("hashmm.") for key in _REQUIRED_PROTOCOLS):
        raise ReleaseManifestError("invalid release manifest protocol identifier")
    minimum = data.get("minimum_compatible")
    if not isinstance(minimum, dict):
        raise ReleaseManifestError("minimum_compatible is required")
    _require_text(minimum, "desktop", _SEMVER)
    _require_text(minimum, "backend_release", _RELEASE)
    if not isinstance(minimum.get("android_version_code"), int):
        raise ReleaseManifestError("minimum android version code is required")
    return data


@lru_cache(maxsize=1)
def release_manifest() -> dict[str, Any]:
    try:
        raw = json.loads(_read_manifest_text())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError("release manifest is missing or unreadable") from exc
    if not isinstance(raw, dict):
        raise ReleaseManifestError("release manifest must be a JSON object")
    return validate_manifest(raw)


MANIFEST = release_manifest()
PRODUCT_VERSION = str(MANIFEST["product_version"])
BACKEND_VERSION = str(MANIFEST["backend_version"])
API_VERSION = str(MANIFEST["api_version"])
DESKTOP_VERSION = str(MANIFEST["desktop_version"])
ANDROID_VERSION_NAME = str(MANIFEST["android_version_name"])
ANDROID_VERSION_CODE = int(MANIFEST["android_version_code"])
RELEASE = str(MANIFEST["release"])
PROTOCOLS: dict[str, str] = dict(MANIFEST["protocols"])
MINIMUM_COMPATIBLE: dict[str, Any] = dict(MANIFEST["minimum_compatible"])


def public_release_info() -> dict[str, Any]:
    """Return the secret-free contract exposed to clients and release checks."""
    return {
        "schema": MANIFEST["schema"],
        "product": MANIFEST["product"],
        "product_version": PRODUCT_VERSION,
        "release": RELEASE,
        "backend_version": BACKEND_VERSION,
        "api_version": API_VERSION,
        "desktop_version": DESKTOP_VERSION,
        "android_version_name": ANDROID_VERSION_NAME,
        "android_version_code": ANDROID_VERSION_CODE,
        "database_schema_version": MANIFEST["database_schema_version"],
        "protocols": dict(PROTOCOLS),
        "minimum_compatible": dict(MINIMUM_COMPATIBLE),
    }


__all__ = [
    "ANDROID_VERSION_CODE", "ANDROID_VERSION_NAME", "API_VERSION", "BACKEND_VERSION",
    "DESKTOP_VERSION", "MANIFEST", "MINIMUM_COMPATIBLE", "PRODUCT_VERSION", "PROTOCOLS",
    "RELEASE", "ReleaseManifestError", "public_release_info", "release_manifest",
    "validate_manifest",
]

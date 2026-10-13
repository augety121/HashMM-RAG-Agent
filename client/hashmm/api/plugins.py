"""Manifest-first plugin inventory and explicitly trusted Python adapters.

Plugin directories are untrusted data during discovery.  HashMM reads only a
bounded ``plugin.json`` and never imports Python merely because a directory was
copied into ``plugins/``.  Executable plugins are bound to an administrator
reviewed SHA-256 receipt; any byte change revokes their active tools.

Trusted Python plugins execute in a short-lived subprocess bound to the exact
reviewed digest.  This isolates interpreter crashes and plugin global state; it
is not an operating-system sandbox.  The normal Agent execution-scope, hook,
approval, security-policy and audit path remains mandatory for every tool call.
MCP remains the preferred boundary for third-party integrations.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("hashmm.plugins")

MANIFEST_SCHEMA = "hashmm.plugin.v1"
TRUST_SCHEMA = "hashmm.plugin-trust.v1"
MANIFEST_STATE_SCHEMA = "hashmm.plugin-manifest-state.v1"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_FILE_BYTES = 1024 * 1024
_MAX_PACKAGE_BYTES = 4 * 1024 * 1024
_MAX_PACKAGE_FILES = 100
_MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
_CTX_KEYS = {
    "user_id", "conv_id", "session_id", "cwd", "approved",
    "permission_prechecked", "execution_scope",
}


class PluginValidationError(ValueError):
    """A plugin cannot be inventoried or loaded safely."""


@dataclass
class PluginInfo:
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    ui: list[str] = field(default_factory=list)
    scheduled_tasks: list[str] = field(default_factory=list)
    compatibility: dict[str, str] = field(default_factory=dict)
    enabled: bool = False
    path: Path | None = None
    entrypoint: str = ""
    runtime: str = "manifest"
    permissions: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, dict[str, Any]] = field(default_factory=dict)
    sha256: str = ""
    trusted: bool = False
    active: bool = False
    status: str = "discovered"
    error: str = ""


@dataclass
class PluginTool:
    """A tool exposed by one exact, trusted plugin snapshot."""

    name: str
    description: str
    parameters: dict
    executor: Callable
    plugin_name: str
    annotation: dict[str, Any]
    plugin_sha256: str


def _data_dir() -> Path:
    raw = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    return Path(raw).expanduser().resolve()


def _inside(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(child), str(parent))) == str(parent)
    except (OSError, ValueError):
        return False


def _read_regular(path: Path, limit: int) -> bytes:
    try:
        meta = path.lstat()
    except FileNotFoundError as exc:
        raise PluginValidationError(f"文件不存在：{path.name}") from exc
    if stat.S_ISLNK(meta.st_mode) or not stat.S_ISREG(meta.st_mode):
        raise PluginValidationError(f"拒绝符号链接或非普通文件：{path.name}")
    if meta.st_size > limit:
        raise PluginValidationError(f"文件超过大小上限：{path.name}")
    data = path.read_bytes()
    if len(data) != meta.st_size or len(data) > limit:
        raise PluginValidationError(f"文件读取期间发生变化：{path.name}")
    after = path.lstat()
    before_identity = (getattr(meta, "st_ino", 0), meta.st_size, meta.st_mtime_ns)
    after_identity = (getattr(after, "st_ino", 0), after.st_size, after.st_mtime_ns)
    if stat.S_ISLNK(after.st_mode) or before_identity != after_identity:
        raise PluginValidationError(f"文件读取期间发生变化：{path.name}")
    return data


def _package_snapshot(root: Path) -> tuple[str, list[str]]:
    root = root.resolve()
    records: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_dir():
            if path.is_symlink():
                raise PluginValidationError("插件目录不能包含符号链接")
            continue
        # Hash every shipped regular file, not only source-looking suffixes.
        # Otherwise executable code could read an untrusted payload that was
        # added or changed after the administrator reviewed the package digest.
        if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        if len(records) >= _MAX_PACKAGE_FILES:
            raise PluginValidationError("插件文件数量超过上限")
        resolved = path.resolve()
        if not _inside(resolved, root):
            raise PluginValidationError("插件文件越过插件目录边界")
        data = _read_regular(path, _MAX_FILE_BYTES)
        total += len(data)
        if total > _MAX_PACKAGE_BYTES:
            raise PluginValidationError("插件包超过 4 MiB 审查上限")
        records.append((path.relative_to(root).as_posix(), data))
    if not records:
        raise PluginValidationError("插件包没有可审查文件")
    digest = hashlib.sha256()
    for relative, data in records:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest(), [name for name, _ in records]


def _safe_annotation(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    required = ("read_only", "destructive", "idempotent", "open_world")
    if any(not isinstance(raw.get(key), bool) for key in required):
        raise PluginValidationError("每个工具必须声明 read_only/destructive/idempotent/open_world 布尔标注")
    if raw["read_only"] and raw["destructive"]:
        raise PluginValidationError("工具不能同时声明只读和破坏性")
    return {key: bool(raw[key]) for key in required} | {
        "title": str(raw.get("title") or "")[:120],
    }


def _manifest_names(raw: Any, field_name: str, *, limit: int = 80) -> list[str]:
    """Project declarative capabilities without trusting arbitrary objects."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PluginValidationError(f"{field_name} 必须是数组")
    result: list[str] = []
    for item in raw[:limit]:
        value = str(item.get("id") or item.get("name") or "") if isinstance(item, dict) else str(item)
        value = value.strip()
        if not _TOOL_RE.fullmatch(value) or value in result:
            raise PluginValidationError(f"{field_name} 包含无效或重复标识：{value or '<empty>'}")
        result.append(value)
    return result


def _lifecycle_state(info: PluginInfo, active: bool) -> str:
    """Stable product lifecycle independent of legacy diagnostic labels."""
    if active:
        return "active"
    if info.status in {"quarantined", "invalid", "collision", "load_failed", "disabled_by_policy"}:
        return "quarantined"
    if info.status in {"revoked", "changed", "manifest_changed"}:
        return "revoked"
    if info.status in {"review_required", "configuration_required"}:
        return "awaiting_approval"
    if info.status == "trusted" or info.trusted:
        return "trusted"
    if info.status == "disabled" or not info.enabled:
        return "inspected"
    return "discovered"


def _parse_manifest(root: Path) -> tuple[PluginInfo, dict[str, dict[str, Any]]]:
    manifest_path = root / "plugin.json"
    data = _read_regular(manifest_path, _MAX_MANIFEST_BYTES)
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginValidationError("plugin.json 不是有效 UTF-8 JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != MANIFEST_SCHEMA:
        raise PluginValidationError(f"plugin.json.schema 必须为 {MANIFEST_SCHEMA}")
    plugin_id = str(raw.get("id") or "").strip()
    if not _NAME_RE.fullmatch(plugin_id) or plugin_id.startswith("_"):
        raise PluginValidationError("插件 id 不合法")
    if plugin_id != root.name:
        raise PluginValidationError("插件 id 必须与目录名一致")
    runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {}
    runtime_kind = str(runtime.get("kind") or "manifest").strip().lower()
    if runtime_kind not in {"manifest", "python"}:
        raise PluginValidationError("仅支持 manifest 或 python 运行时；远程插件请使用 MCP")
    entrypoint = str(runtime.get("entrypoint") or "").strip()
    if runtime_kind == "python":
        if not entrypoint or Path(entrypoint).is_absolute() or Path(entrypoint).suffix.lower() != ".py":
            raise PluginValidationError("Python 插件必须声明相对 .py entrypoint")
        resolved_entry = (root / entrypoint).resolve()
        if not _inside(resolved_entry, root.resolve()):
            raise PluginValidationError("插件 entrypoint 越过插件目录边界")
        _read_regular(resolved_entry, _MAX_FILE_BYTES)
    tools: dict[str, dict[str, Any]] = {}
    for item in raw.get("tools") or []:
        if not isinstance(item, dict):
            raise PluginValidationError("tools 项必须是对象")
        name = str(item.get("name") or "").strip()
        if not _TOOL_RE.fullmatch(name) or name in tools:
            raise PluginValidationError(f"工具名无效或重复：{name or '<empty>'}")
        parameters = item.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise PluginValidationError(f"工具 {name} 缺少 object 参数 schema")
        tools[name] = {
            "name": name,
            "description": str(item.get("description") or "")[:1000],
            "parameters": parameters,
            "annotation": _safe_annotation(item.get("annotations")),
        }
    if runtime_kind == "python" and not tools:
        raise PluginValidationError("可执行插件至少要声明一个工具")
    permissions = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
    filesystem_permission = str(permissions.get("filesystem") or "none").strip().lower()
    network_permission = str(permissions.get("network") or "none").strip().lower()
    if filesystem_permission not in {"none", "plugin_read", "workspace_read", "workspace_write"}:
        raise PluginValidationError("permissions.filesystem 不受支持")
    if network_permission not in {"none", "read_only_via_scheduler", "https_allowlist", "open"}:
        raise PluginValidationError("permissions.network 不受支持")
    compatibility = raw.get("compatibility") if isinstance(raw.get("compatibility"), dict) else {}
    info = PluginInfo(
        name=plugin_id,
        version=str(raw.get("version") or "")[:80],
        description=str(raw.get("description") or "")[:1000],
        author=str(raw.get("author") or "")[:160],
        capabilities=[str(item)[:120] for item in (raw.get("capabilities") or [])[:40]],
        tools=list(tools),
        skills=_manifest_names(raw.get("skills"), "skills"),
        connectors=_manifest_names(raw.get("connectors"), "connectors"),
        hooks=_manifest_names(raw.get("hooks"), "hooks"),
        ui=_manifest_names(raw.get("ui"), "ui"),
        scheduled_tasks=_manifest_names(raw.get("scheduled_tasks"), "scheduled_tasks"),
        compatibility={
            "hashmm": str(compatibility.get("hashmm") or "")[:80],
            "protocol": str(compatibility.get("protocol") or "")[:80],
        },
        enabled=bool(raw.get("enabled", True)),
        path=root.resolve(),
        entrypoint=entrypoint,
        runtime=runtime_kind,
        permissions={
            "filesystem": filesystem_permission,
            "network": network_permission,
            "side_effects": bool(permissions.get("side_effects", False)),
        },
        annotations={name: dict(tool["annotation"]) for name, tool in tools.items()},
    )
    return info, tools


class PluginManager:
    """Discover manifests, verify exact trust, and expose active tools."""

    def __init__(self, plugins_dir: str | Path = "plugins"):
        self.plugins_dir = Path(plugins_dir).expanduser().resolve()
        self.plugins: dict[str, PluginInfo] = {}
        self.tools: dict[str, PluginTool] = {}
        self._declared: dict[str, dict[str, dict[str, Any]]] = {}
        self._loaded_modules: dict[str, Any] = {}
        self._lock = threading.RLock()

    @property
    def trust_path(self) -> Path:
        return _data_dir() / "plugin-trust.json"

    @property
    def manifest_state_path(self) -> Path:
        return _data_dir() / "plugin-manifest-state.json"

    def _read_manifest_state(self) -> dict[str, dict[str, Any]]:
        try:
            path = self.manifest_state_path
            if not path.is_file() or path.is_symlink():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema") != MANIFEST_STATE_SCHEMA:
                return {}
            result: dict[str, dict[str, Any]] = {}
            for name, item in (raw.get("plugins") or {}).items():
                digest = str((item or {}).get("sha256") or "").lower()
                if _NAME_RE.fullmatch(str(name)) and _SHA_RE.fullmatch(digest):
                    result[str(name)] = {
                        "sha256": digest,
                        "activated_at": float((item or {}).get("activated_at") or 0),
                        "activated_by": str((item or {}).get("activated_by") or "")[:160],
                    }
            return result
        except Exception:
            return {}

    def _write_manifest_state(self, entries: dict[str, dict[str, Any]]) -> None:
        root = self.manifest_state_path.parent
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": MANIFEST_STATE_SCHEMA, "plugins": entries},
            ensure_ascii=False, indent=2, sort_keys=True,
        ).encode("utf-8")
        temporary = root / f".plugin-manifest-state.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(str(temporary), flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, self.manifest_state_path)
            try:
                os.chmod(self.manifest_state_path, 0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _read_trust(self) -> dict[str, dict[str, Any]]:
        try:
            path = self.trust_path
            if not path.is_file() or path.is_symlink():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema") != TRUST_SCHEMA:
                return {}
            result: dict[str, dict[str, Any]] = {}
            for name, item in (raw.get("plugins") or {}).items():
                digest = str((item or {}).get("sha256") or "").lower()
                if _NAME_RE.fullmatch(str(name)) and _SHA_RE.fullmatch(digest):
                    result[str(name)] = {
                        "sha256": digest,
                        "trusted_at": float((item or {}).get("trusted_at") or 0),
                        "trusted_by": str((item or {}).get("trusted_by") or "")[:160],
                    }
            return result
        except Exception:
            return {}

    def _write_trust(self, entries: dict[str, dict[str, Any]]) -> None:
        root = self.trust_path.parent
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": TRUST_SCHEMA, "plugins": entries},
            ensure_ascii=False, indent=2, sort_keys=True,
        ).encode("utf-8")
        temporary = root / f".plugin-trust.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(str(temporary), flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, self.trust_path)
            try:
                os.chmod(self.trust_path, 0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def discover(self) -> list[PluginInfo]:
        """Read bounded manifests only.  No plugin code executes here."""
        with self._lock:
            previous = dict(self.plugins)
            self.plugins = {}
            self._declared = {}
            if not self.plugins_dir.is_dir():
                return []
            receipts = self._read_trust()
            manifest_states = self._read_manifest_state()
            for directory in sorted(self.plugins_dir.iterdir(), key=lambda p: p.name.lower()):
                if not directory.is_dir() or directory.name.startswith("_"):
                    continue
                try:
                    if directory.is_symlink():
                        raise PluginValidationError("插件目录不能是符号链接")
                    info, declared = _parse_manifest(directory)
                    digest, _ = _package_snapshot(directory)
                    info.sha256 = digest
                    receipt = receipts.get(info.name) or {}
                    info.trusted = bool(receipt) and secrets.compare_digest(
                        str(receipt.get("sha256") or ""), digest,
                    )
                    if not info.enabled:
                        info.status = "disabled"
                    elif info.runtime == "manifest":
                        activation = manifest_states.get(info.name) or {}
                        if activation and secrets.compare_digest(
                            str(activation.get("sha256") or ""), digest,
                        ):
                            info.status = "active_manifest"
                            info.active = True
                        elif activation:
                            info.status = "manifest_changed"
                        else:
                            info.status = "configuration_required"
                    elif info.trusted:
                        info.status = "trusted"
                    elif receipt:
                        info.status = "changed"
                    else:
                        info.status = "review_required"
                    old = previous.get(info.name)
                    if (old is not None and old.sha256 == info.sha256
                            and old.status in {"collision", "load_failed", "disabled_by_policy"}):
                        # Keep the actionable runtime diagnosis until bytes or
                        # policy change; rediscovery must not erase the reason
                        # an administrator's load action failed.
                        info.status = old.status
                        info.error = old.error
                    self.plugins[info.name] = info
                    self._declared[info.name] = declared
                except Exception as exc:
                    name = directory.name if _NAME_RE.fullmatch(directory.name) else f"invalid-{len(self.plugins) + 1}"
                    self.plugins[name] = PluginInfo(
                        name=name, path=directory.resolve(), enabled=False,
                        status="invalid", error=str(exc)[:300],
                    )
            return list(self.plugins.values())

    def trust(self, name: str, expected_sha256: str, *, trusted_by: str = "") -> PluginInfo:
        """Trust exact reviewed bytes.  This method never imports the plugin."""
        self.discover()
        info = self.plugins.get(str(name or ""))
        expected = str(expected_sha256 or "").strip().lower()
        if not info or info.status == "invalid":
            raise PluginValidationError("插件不存在或清单无效")
        if info.runtime != "python":
            raise PluginValidationError("声明式插件不执行代码，不进入 Python 摘要信任流程")
        if not _SHA_RE.fullmatch(expected) or not secrets.compare_digest(expected, info.sha256):
            raise PluginValidationError("插件内容已变化；请刷新清单并重新核对 SHA-256")
        entries = self._read_trust()
        entries[info.name] = {
            "sha256": info.sha256,
            "trusted_at": time.time(),
            "trusted_by": str(trusted_by or "")[:160],
        }
        self._write_trust(entries)
        self.discover()
        return self.plugins[info.name]

    def set_manifest_active(
        self, name: str, expected_sha256: str, *, active: bool, actor: str = "",
    ) -> PluginInfo:
        """Activate declarative configuration without granting code trust."""
        with self._lock:
            self.discover()
            info = self.plugins.get(str(name or ""))
            expected = str(expected_sha256 or "").strip().lower()
            if not info or info.status == "invalid" or info.runtime != "manifest":
                raise PluginValidationError("声明式插件不存在或清单无效")
            if not _SHA_RE.fullmatch(expected) or not secrets.compare_digest(expected, info.sha256):
                raise PluginValidationError("声明式插件内容已变化；请刷新后重新核对权限")
            entries = self._read_manifest_state()
            if active:
                entries[info.name] = {
                    "sha256": info.sha256,
                    "activated_at": time.time(),
                    "activated_by": str(actor or "")[:160],
                }
            else:
                entries.pop(info.name, None)
            self._write_manifest_state(entries)
            self.discover()
            return self.plugins[info.name]

    def install_zip(
        self,
        package: bytes,
        *,
        expected_archive_sha256: str = "",
        replace: bool = False,
        installed_by: str = "",
    ) -> dict[str, Any]:
        """Stage and inventory an uploaded ZIP without importing plugin code.

        The archive must contain exactly one plugin root whose directory name
        matches plugin.json.id. Existing packages are replaced only when the
        caller explicitly requests it; the previous bytes are quarantined for
        recovery and their trust receipt is revoked before rediscovery.
        """
        if not package or len(package) > _MAX_ARCHIVE_BYTES:
            raise PluginValidationError("插件 ZIP 为空或超过 8 MiB")
        archive_digest = hashlib.sha256(package).hexdigest()
        expected = str(expected_archive_sha256 or "").strip().lower()
        if expected and (not _SHA_RE.fullmatch(expected) or not secrets.compare_digest(expected, archive_digest)):
            raise PluginValidationError("上传内容与客户端 SHA-256 不一致")

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        staging = (self.plugins_dir / f"_incoming_{secrets.token_hex(12)}").resolve()
        if not _inside(staging, self.plugins_dir):
            raise PluginValidationError("插件暂存目录越界")
        staging.mkdir(mode=0o700)
        backup: Path | None = None
        destination: Path | None = None
        installed_name = ""
        try:
            try:
                archive = zipfile.ZipFile(io.BytesIO(package))
            except (zipfile.BadZipFile, OSError) as exc:
                raise PluginValidationError("上传文件不是有效 ZIP") from exc
            with archive:
                files = [item for item in archive.infolist() if not item.is_dir()]
                if not files or len(files) > _MAX_PACKAGE_FILES:
                    raise PluginValidationError("插件文件数量必须为 1 到 100")
                total = 0
                seen_paths: set[str] = set()
                for item in archive.infolist():
                    name = item.filename
                    if not name or "\\" in name or name.startswith("/"):
                        raise PluginValidationError("ZIP 包含非法路径")
                    parts = [part for part in name.split("/") if part not in {"", "."}]
                    if not parts or any(part == ".." for part in parts):
                        raise PluginValidationError("ZIP 包含目录穿越路径")
                    # Windows treats case-only names as the same path and ':' as
                    # an alternate data stream separator. Reject both here so
                    # the reviewed snapshot is identical on every supported OS.
                    if any(
                        ":" in part or part.endswith((".", " ")) or any(ord(ch) < 32 for ch in part)
                        for part in parts
                    ):
                        raise PluginValidationError("ZIP 包含跨平台不安全路径")
                    normalized_path = "/".join(parts).casefold()
                    if normalized_path in seen_paths:
                        raise PluginValidationError("ZIP 包含重复或大小写冲突路径")
                    seen_paths.add(normalized_path)
                    mode = (item.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise PluginValidationError("ZIP 不能包含符号链接")
                    target = staging.joinpath(*parts).resolve()
                    if not _inside(target, staging):
                        raise PluginValidationError("ZIP 解压路径越界")
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if item.file_size > _MAX_FILE_BYTES:
                        raise PluginValidationError(f"文件超过 1 MiB：{parts[-1]}")
                    total += int(item.file_size)
                    if total > _MAX_PACKAGE_BYTES:
                        raise PluginValidationError("插件解压后超过 4 MiB")
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item, "r") as source, target.open("xb") as output:
                            data = source.read(_MAX_FILE_BYTES + 1)
                            if len(data) != item.file_size or len(data) > _MAX_FILE_BYTES:
                                raise PluginValidationError(f"文件解压大小异常：{parts[-1]}")
                            output.write(data)
                    except FileExistsError as exc:
                        raise PluginValidationError("ZIP 包含文件与目录冲突") from exc

            manifests = list(staging.rglob("plugin.json"))
            if len(manifests) != 1:
                raise PluginValidationError("ZIP 必须且只能包含一个 plugin.json")
            plugin_root = manifests[0].parent.resolve()
            if plugin_root.parent != staging:
                raise PluginValidationError("ZIP 顶层必须是以插件 id 命名的单一目录")
            for path in staging.rglob("*"):
                if path.is_file() and not _inside(path.resolve(), plugin_root):
                    raise PluginValidationError("ZIP 在插件根目录之外包含额外文件")
            info, _ = _parse_manifest(plugin_root)
            package_digest, package_files = _package_snapshot(plugin_root)
            installed_name = info.name
            destination = (self.plugins_dir / info.name).resolve()
            if not _inside(destination, self.plugins_dir):
                raise PluginValidationError("插件目标目录越界")
            if destination.exists() and not replace:
                raise PluginValidationError("同名插件已存在；如需升级请明确选择替换并重新审核")

            with self._lock:
                old_digest = ""
                if destination.exists():
                    try:
                        old_digest, _ = _package_snapshot(destination)
                    except Exception:
                        old_digest = "unreadable"
                    quarantine_root = (_data_dir() / "plugin-quarantine").resolve()
                    quarantine_root.mkdir(parents=True, exist_ok=True)
                    backup = quarantine_root / f"{info.name}-{int(time.time())}-{old_digest[:12]}-{secrets.token_hex(4)}"
                    self._remove_tools(info.name)
                    receipts = self._read_trust()
                    receipts.pop(info.name, None)
                    self._write_trust(receipts)
                    os.replace(destination, backup)
                try:
                    os.replace(plugin_root, destination)
                except Exception:
                    if backup and backup.exists() and destination and not destination.exists():
                        os.replace(backup, destination)
                    raise
                self.discover()
            return {
                "ok": True,
                "action": "upgraded" if backup else "installed",
                "archive_sha256": archive_digest,
                "package_sha256": package_digest,
                "file_count": len(package_files),
                "plugin": next(item for item in self.list_plugins() if item["name"] == installed_name),
                "trust_required": info.runtime == "python",
                "activation_required": info.runtime == "manifest",
                "execution_started": False,
                "installed_by": str(installed_by or "")[:160],
                "previous_quarantined": bool(backup),
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def diagnostics(self, name: str) -> dict[str, Any]:
        self.discover()
        info = self.plugins.get(str(name or ""))
        if not info or not info.path:
            raise PluginValidationError("插件不存在")
        digest, files = _package_snapshot(info.path)
        receipts = self._read_trust()
        receipt = receipts.get(info.name) or {}
        return {
            "schema": "hashmm.plugin-diagnostics.v1",
            "name": info.name,
            "version": info.version,
            "runtime": info.runtime,
            "status": info.status,
            "active": info.active or any(tool.plugin_name == info.name for tool in self.tools.values()),
            "sha256": digest,
            "file_count": len(files),
            "files": files,
            "permissions": dict(info.permissions),
            "tools": [
                {"name": tool, "annotations": dict(info.annotations.get(tool) or {})}
                for tool in info.tools
            ],
            "trust": {
                "trusted": bool(receipt) and secrets.compare_digest(str(receipt.get("sha256") or ""), digest),
                "trusted_at": float(receipt.get("trusted_at") or 0),
                "trusted_by": str(receipt.get("trusted_by") or ""),
            },
            "execution_boundary": "subprocess_exact_digest_readonly_no_network_audit_policy" if info.runtime == "python" else "declarative_no_code_execution",
            "error": info.error,
        }

    def revoke(self, name: str) -> bool:
        with self._lock:
            entries = self._read_trust()
            existed = str(name or "") in entries
            entries.pop(str(name or ""), None)
            self._write_trust(entries)
            self._remove_tools(str(name or ""))
            self._loaded_modules.pop(str(name or ""), None)
            self.discover()
            return existed

    def quarantine(self, name: str, *, actor: str = "") -> dict[str, Any]:
        """Recoverably remove a plugin directory from discovery.

        The directory is moved under ProjectVault; it is never recursively
        deleted and no plugin bytes are imported while deciding this action.
        """
        with self._lock:
            self.discover()
            info = self.plugins.get(str(name or ""))
            if not info or not info.path:
                raise PluginValidationError("插件不存在")
            source = info.path.resolve()
            if not _inside(source, self.plugins_dir) or source.parent != self.plugins_dir:
                raise PluginValidationError("插件目录不在受管边界内")
            quarantine_root = (_data_dir() / "plugin-quarantine").resolve()
            quarantine_root.mkdir(parents=True, exist_ok=True)
            destination = quarantine_root / f"{source.name}-{int(time.time())}-{secrets.token_hex(4)}"
            if not _inside(destination.resolve(), quarantine_root):
                raise PluginValidationError("隔离目录越界")
            self._remove_tools(info.name)
            trust = self._read_trust()
            trust.pop(info.name, None)
            self._write_trust(trust)
            manifests = self._read_manifest_state()
            manifests.pop(info.name, None)
            self._write_manifest_state(manifests)
            os.replace(source, destination)
            self.discover()
            return {
                "ok": True,
                "name": info.name,
                "quarantined": True,
                "quarantine_id": destination.name,
                "actor": str(actor or "")[:160],
            }

    def _remove_tools(self, plugin_name: str) -> None:
        for tool_name in [name for name, tool in self.tools.items() if tool.plugin_name == plugin_name]:
            self.tools.pop(tool_name, None)
        info = self.plugins.get(plugin_name)
        if info:
            info.active = False

    def _reserved_tool_names(self) -> set[str]:
        names: set[str] = set()
        try:
            from hashmm.api.tool_registry import TOOL_DEFS, get_executor_map
            names.update(str(item.get("function", {}).get("name") or "") for item in TOOL_DEFS)
            names.update(get_executor_map())
        except Exception:
            pass
        names.discard("")
        return names

    def _worker_call(self, info: PluginInfo, action: str, *, tool: str = "", args: dict | None = None, ctx: dict | None = None) -> Any:
        if not info.path:
            raise PluginValidationError("插件路径不存在")
        current, _ = _package_snapshot(info.path)
        if not secrets.compare_digest(current, info.sha256):
            raise PluginValidationError("插件在执行前发生变化")
        worker = Path(__file__).with_name("plugin_worker.py").resolve()
        request = {
            "action": action,
            "package_root": str(info.path.resolve()),
            "entrypoint": str((info.path / info.entrypoint).resolve()),
            "digest": info.sha256,
            "tool": tool,
            "args": dict(args or {}),
            "ctx": {key: (ctx or {}).get(key) for key in _CTX_KEYS if key in (ctx or {})},
            "permissions": dict(info.permissions),
        }
        timeout = max(1.0, min(120.0, float(os.environ.get("HASHMM_PLUGIN_TIMEOUT_SECONDS", "30"))))
        allowed_env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "PYTHONUTF8"}
        }
        allowed_env["PYTHONUTF8"] = "1"
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(worker)],
                input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env=allowed_env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginValidationError(f"插件执行超过 {timeout:g} 秒，工作进程已终止") from exc
        if completed.returncode != 0:
            raise PluginValidationError(f"插件工作进程异常退出（code={completed.returncode}）")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except Exception as exc:
            raise PluginValidationError("插件工作进程返回了无效协议") from exc
        if not response.get("ok"):
            raise PluginValidationError(str(response.get("error") or "插件执行失败")[:300])
        current_after, _ = _package_snapshot(info.path)
        if not secrets.compare_digest(current_after, info.sha256):
            raise PluginValidationError("插件在执行期间发生变化")
        return response.get("result")

    def load(self, name: str) -> bool:
        """Probe one exact trusted package and register subprocess executors."""
        with self._lock:
            self.discover()
            info = self.plugins.get(str(name or ""))
            if not info or not info.enabled or info.runtime != "python" or not info.trusted or not info.path:
                return False
            if os.environ.get("HASHMM_PLUGINS_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
                info.status = "disabled_by_policy"
                return False
            if info.permissions.get("filesystem") != "none" or info.permissions.get("network") != "none":
                info.status = "configuration_required"
                info.error = "需要文件或网络权限的插件必须使用 MCP/隔离工作区运行时"
                return False
            collisions = set(info.tools) & (self._reserved_tool_names() | (set(self.tools) - set(
                name for name, tool in self.tools.items() if tool.plugin_name == info.name
            )))
            if collisions:
                info.status = "collision"
                info.error = "工具名与现有能力冲突：" + ", ".join(sorted(collisions))
                return False
            try:
                current, _ = _package_snapshot(info.path)
                if not secrets.compare_digest(current, info.sha256):
                    raise PluginValidationError("插件在加载前发生变化")
                described = self._worker_call(info, "describe")
                registered = described.get("tools") if isinstance(described, dict) else None
                if not isinstance(registered, list):
                    raise PluginValidationError("插件工作进程未返回工具列表")
                declared = self._declared.get(info.name) or {}
                prepared: dict[str, PluginTool] = {}
                for raw in registered:
                    tool_name = str(raw or "")
                    declaration = declared.get(tool_name)
                    if not declaration or tool_name in prepared:
                        raise PluginValidationError(f"插件注册了未声明、重复或不可执行的工具：{tool_name}")

                    def _scoped_executor(args: dict, ctx: dict, _info=info, _tool=tool_name):
                        return self._worker_call(_info, "execute", tool=_tool, args=args, ctx=ctx)

                    prepared[tool_name] = PluginTool(
                        name=tool_name,
                        description=declaration["description"],
                        parameters=dict(declaration["parameters"]),
                        executor=_scoped_executor,
                        plugin_name=info.name,
                        annotation=dict(declaration["annotation"]),
                        plugin_sha256=info.sha256,
                    )
                if set(prepared) != set(declared):
                    missing = sorted(set(declared) - set(prepared))
                    raise PluginValidationError("插件未注册清单中的工具：" + ", ".join(missing))
                self._remove_tools(info.name)
                self.tools.update(prepared)
                info.active = True
                info.status = "active"
                info.error = ""
                logger.info("trusted plugin loaded: %s sha256=%s tools=%d", info.name, info.sha256, len(prepared))
                return True
            except Exception as exc:
                self._remove_tools(info.name)
                info.status = "load_failed"
                info.error = str(exc)[:300]
                logger.error("failed to load trusted plugin %s: %s", info.name, exc)
                return False

    def load_all(self) -> list[str]:
        active: list[str] = []
        for info in self.discover():
            if info.enabled and info.runtime == "python" and info.trusted and self.load(info.name):
                active.append(info.name)
        return active

    def ensure_loaded(self) -> None:
        """Lazy startup hook used by AgentLoop; only trusted snapshots can run."""
        if not self.plugins:
            self.load_all()
            return
        receipts = self._read_trust()
        for info in list(self.plugins.values()):
            tool_active = any(tool.plugin_name == info.name for tool in self.tools.values())
            exact = str((receipts.get(info.name) or {}).get("sha256") or "")
            if not tool_active:
                continue
            try:
                current, _ = _package_snapshot(info.path) if info.path else ("", [])
            except Exception:
                current = ""
            if (not exact or not current
                    or not secrets.compare_digest(exact, info.sha256)
                    or not secrets.compare_digest(current, info.sha256)):
                self._remove_tools(info.name)
                info.status = "changed" if exact else "review_required"
                info.error = "活动插件内容或信任收据已变化，工具已立即停用"

    def get_tool_definitions(self, plugin_names: set[str] | None = None) -> list[dict]:
        self.ensure_loaded()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"[插件：{tool.plugin_name}] {tool.description}",
                    "parameters": tool.parameters,
                },
            }
            for tool in sorted(self.tools.values(), key=lambda item: item.name)
            if plugin_names is None or tool.plugin_name in plugin_names
        ]

    def get_executors(self, plugin_names: set[str] | None = None) -> dict[str, Callable]:
        self.ensure_loaded()
        return {
            name: tool.executor
            for name, tool in self.tools.items()
            if plugin_names is None or tool.plugin_name in plugin_names
        }

    def get_tool_annotation(self, name: str) -> dict[str, Any] | None:
        self.ensure_loaded()
        tool = self.tools.get(str(name or ""))
        return dict(tool.annotation) if tool else None

    def list_plugins(self) -> list[dict[str, Any]]:
        self.discover()
        # Revalidate active snapshots before reporting them.  Listing is an
        # important security boundary too: changed bytes must disable tools
        # immediately, not only when the next Agent happens to ask for tools.
        self.ensure_loaded()
        return [
            {
                "name": item.name,
                "version": item.version,
                "description": item.description,
                "author": item.author,
                "enabled": item.enabled,
                "runtime": item.runtime,
                "capabilities": item.capabilities,
                "permissions": item.permissions,
                "tools": item.tools,
                "skills": item.skills,
                "connectors": item.connectors,
                "hooks": item.hooks,
                "ui": item.ui,
                "scheduled_tasks": item.scheduled_tasks,
                "compatibility": item.compatibility,
                "sha256": item.sha256,
                "trusted": item.trusted,
                "active": item.active or any(tool.plugin_name == item.name for tool in self.tools.values()),
                "status": (
                    "active" if (item.active or any(tool.plugin_name == item.name for tool in self.tools.values()))
                    else item.status
                ),
                "lifecycle_state": _lifecycle_state(
                    item,
                    bool(item.active or any(tool.plugin_name == item.name for tool in self.tools.values())),
                ),
                "error": item.error,
                "execution_boundary": (
                    "subprocess_exact_digest_readonly_no_network_audit_policy" if item.runtime == "python" else "declarative_no_code_execution"
                ),
            }
            for item in sorted(self.plugins.values(), key=lambda value: value.name.lower())
        ]

    def execute_tool(self, name: str, args: dict, ctx: dict) -> Any:
        """Compatibility path; callers should normally use AgentLoop's central boundary."""
        tool = self.tools.get(str(name or ""))
        if not tool:
            return f"Error: Plugin tool '{name}' not found"
        return tool.executor(dict(args or {}), dict(ctx or {}))

    def reset_for_tests(self) -> None:
        with self._lock:
            self.plugins.clear()
            self.tools.clear()
            self._declared.clear()
            self._loaded_modules.clear()


_plugin_manager = PluginManager()


def get_plugin_manager() -> PluginManager:
    return _plugin_manager

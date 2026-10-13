"""Trusted local Python hooks for the HashMM agent lifecycle.

Python hooks are executable code, not prompt text.  A hook in ``data/hooks``
therefore MUST NOT run merely because a file appeared there.  HashMM binds
trust to the exact SHA-256 of the reviewed file; new or changed files stay
inactive until an administrator trusts the current digest.

Supported functions inside a trusted ``*.py`` hook::

    def pre_tool(name: str, args: dict, ctx: dict):
        # None = observe/allow; {"deny": True, "reason": "..."} = deny.

    def post_tool(name: str, args: dict, ctx: dict, ok: bool, latency_ms: int):
        # Observe the completed call.  Return value is ignored.

    def on_finish(summary: dict, ctx: dict):
        # Observe one completed Agent turn.  Return value is ignored.

Security properties:

* untrusted, changed, missing, oversized, symlinked, or syntactically invalid
  hooks are never imported;
* trust records are written atomically and malformed records fail closed;
* a loaded hook re-checks the on-disk digest before every callback, so changing
  or revoking it disables the callback immediately;
* pre hooks may only deny or observe.  The old ``{"args": ...}`` rewrite path
  was removed because it ran after permission checks and could replace a safe
  command with a more privileged one without re-evaluation;
* trusted Python still runs with the backend process' full OS permissions.
  Trust is an explicit-code-review boundary, not a sandbox.

``HASHMM_USER_HOOKS=0`` disables even trusted hooks.  Newly trusted hooks are
loaded on the next backend restart; revocation and digest mismatch take effect
immediately for hooks already loaded in the current process.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
import types
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.user_hooks")

_MAX_HOOK_BYTES = 256 * 1024
_TRUST_SCHEMA = 1
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FUNCTIONS = {
    "pre_tool": "PreToolUse",
    "post_tool": "PostToolUse",
    "on_finish": "Stop",
}

# name -> {module, sha256, source}.  Old wrappers remain registered when a hook
# is replaced in-process, but their digest check makes them inert.
_loaded: dict[str, dict[str, object]] = {}
_registered: set[tuple[str, str]] = set()
_load_done = False
_lock = threading.RLock()
_SOFT_LIMIT_S = 2.0


class HookTrustError(ValueError):
    """A hook cannot be trusted because the reviewed artifact is not exact/safe."""


def _enabled() -> bool:
    return (os.environ.get("HASHMM_USER_HOOKS", "1") or "1") != "0"


def _data_dir() -> Path:
    raw = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    return Path(raw).expanduser().resolve()


def _hooks_dir() -> Path:
    return _data_dir() / "hooks"


def _trust_path() -> Path:
    # Keep the receipt outside the executable directory.  This does not protect
    # against the local OS user, but avoids treating a hook payload as metadata.
    return _data_dir() / "hook-trust.json"


def _safe_name(name: str) -> str:
    name = str(name or "").strip()
    if not _NAME_RE.fullmatch(name) or name.startswith("_"):
        raise HookTrustError("Hook 名称不合法")
    return name


def _hook_path(name: str) -> Path:
    return _hooks_dir() / f"{_safe_name(name)}.py"


def _read_snapshot(path: Path) -> tuple[bytes, os.stat_result]:
    """Read one bounded regular-file snapshot without following a known symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise HookTrustError("Hook 文件不存在") from exc
    if stat.S_ISLNK(before.st_mode):
        raise HookTrustError("拒绝符号链接 Hook")
    if not stat.S_ISREG(before.st_mode):
        raise HookTrustError("Hook 必须是普通文件")
    if before.st_size > _MAX_HOOK_BYTES:
        raise HookTrustError(f"Hook 超过 {_MAX_HOOK_BYTES // 1024} KiB 上限")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except (OSError, ValueError) as exc:
        raise HookTrustError("无法安全打开 Hook 文件") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_HOOK_BYTES:
            raise HookTrustError("Hook 不是受支持的普通文件")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, _MAX_HOOK_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_HOOK_BYTES:
                raise HookTrustError(f"Hook 超过 {_MAX_HOOK_BYTES // 1024} KiB 上限")
        data = b"".join(chunks)
    finally:
        os.close(fd)

    # Windows has no portable O_NOFOLLOW.  Re-check the directory entry after
    # reading and reject any swap detected by identity/size/timestamp changes.
    try:
        after = path.lstat()
    except FileNotFoundError as exc:
        raise HookTrustError("Hook 读取期间被删除") from exc
    if stat.S_ISLNK(after.st_mode):
        raise HookTrustError("拒绝符号链接 Hook")
    identity_before = (getattr(before, "st_ino", 0), before.st_size, before.st_mtime_ns)
    identity_after = (getattr(after, "st_ino", 0), after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(data) != after.st_size:
        raise HookTrustError("Hook 读取期间发生变化，请重新审查")
    return data, after


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_trust() -> dict[str, dict]:
    p = _trust_path()
    try:
        if not p.is_file() or p.is_symlink():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != _TRUST_SCHEMA:
            return {}
        entries = raw.get("hooks")
        if not isinstance(entries, dict):
            return {}
        clean: dict[str, dict] = {}
        for name, entry in entries.items():
            try:
                safe = _safe_name(name)
            except HookTrustError:
                continue
            if not isinstance(entry, dict):
                continue
            sha = str(entry.get("sha256") or "").lower()
            if not _SHA256_RE.fullmatch(sha):
                continue
            clean[safe] = {
                "sha256": sha,
                "trusted_at": float(entry.get("trusted_at") or 0),
                "trusted_by": str(entry.get("trusted_by") or "")[:160],
            }
        return clean
    except Exception as exc:  # malformed/missing trust is always untrusted
        log_suppressed(logger, exc, "user_hooks.trust_read")
        return {}


def _write_trust(entries: dict[str, dict]) -> None:
    root = _data_dir()
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema": _TRUST_SCHEMA, "hooks": entries},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    dst = _trust_path()
    tmp = root / f".hook-trust.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, dst)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _declared_events(data: bytes) -> tuple[list[str], str]:
    try:
        tree = ast.parse(data, filename="<hook>")
    except (SyntaxError, UnicodeError) as exc:
        return [], f"语法无效：{exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__}"
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return [_EVENT_FUNCTIONS[n] for n in _EVENT_FUNCTIONS if n in names], ""


def _current_digest(name: str) -> str:
    data, _ = _read_snapshot(_hook_path(name))
    return _digest(data)


def _is_active(name: str, digest: str) -> bool:
    """Exact trust + exact current bytes.  Any error is an immediate deny/skip."""
    if not _enabled():
        return False
    try:
        entry = _read_trust().get(name) or {}
        return secrets.compare_digest(str(entry.get("sha256") or ""), digest) and secrets.compare_digest(
            _current_digest(name), digest
        )
    except Exception as exc:
        log_suppressed(logger, exc, f"user_hooks.active({name})")
        return False


def list_hook_inventory() -> list[dict]:
    """Return auditable hook sources without importing any untrusted code."""
    entries = _read_trust()
    items: list[dict] = []
    seen: set[str] = set()
    d = _hooks_dir()
    paths = sorted(d.glob("*.py"), key=lambda p: p.name.lower()) if d.is_dir() else []
    for path in paths:
        name = path.stem
        if name.startswith("_") or not _NAME_RE.fullmatch(name):
            continue
        seen.add(name)
        entry = entries.get(name)
        try:
            data, st = _read_snapshot(path)
            sha = _digest(data)
            events, syntax_error = _declared_events(data)
            exact = bool(entry) and secrets.compare_digest(str(entry.get("sha256") or ""), sha)
            loaded = _loaded.get(name) or {}
            active = exact and loaded.get("sha256") == sha and _is_active(name, sha)
            if not _enabled():
                status = "disabled"
            elif syntax_error:
                status = "invalid"
            elif not entry:
                status = "untrusted"
            elif not exact:
                status = "changed"
            elif active:
                status = "active"
            else:
                status = "trusted_pending_restart"
            items.append({
                "name": name,
                "source": f"hooks/{path.name}",
                "sha256": sha,
                "size": len(data),
                "modified": st.st_mtime,
                "events": events,
                "trusted": exact,
                "active": active,
                "receipt_present": bool(entry),
                "trusted_at": float((entry or {}).get("trusted_at") or 0),
                "status": status,
                "error": syntax_error,
            })
        except HookTrustError as exc:
            items.append({
                "name": name,
                "source": f"hooks/{path.name}",
                "sha256": "",
                "size": 0,
                "modified": 0,
                "events": [],
                "trusted": False,
                "active": False,
                "receipt_present": bool(entry),
                "trusted_at": float((entry or {}).get("trusted_at") or 0),
                "status": "unsafe",
                "error": str(exc),
            })

    # Keep stale receipts visible so an administrator can revoke them.
    for name, entry in sorted(entries.items()):
        if name in seen:
            continue
        items.append({
            "name": name,
            "source": f"hooks/{name}.py",
            "sha256": "",
            "size": 0,
            "modified": 0,
            "events": [],
            "trusted": False,
            "active": False,
            "receipt_present": True,
            "trusted_at": float(entry.get("trusted_at") or 0),
            "status": "missing",
            "error": "已信任的 Hook 文件不存在",
        })
    return items


def trust_hook(name: str, expected_sha256: str, *, trusted_by: str = "") -> dict:
    """Trust exactly the reviewed bytes.  Does not execute the module."""
    name = _safe_name(name)
    expected = str(expected_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise HookTrustError("expected_sha256 必须是完整的 64 位 SHA-256")
    data, _ = _read_snapshot(_hook_path(name))
    actual = _digest(data)
    if not secrets.compare_digest(actual, expected):
        raise HookTrustError("Hook 内容已变化，拒绝信任；请刷新后重新审查")
    try:
        compile(data, str(_hook_path(name)), "exec")
    except (SyntaxError, UnicodeError) as exc:
        raise HookTrustError(f"Hook 语法无效：{exc}") from exc
    with _lock:
        entries = _read_trust()
        entries[name] = {
            "sha256": actual,
            "trusted_at": time.time(),
            "trusted_by": str(trusted_by or "")[:160],
        }
        _write_trust(entries)
    return next(item for item in list_hook_inventory() if item["name"] == name)


def revoke_hook(name: str) -> bool:
    """Revoke a receipt.  Loaded wrappers become inert on their next callback."""
    name = _safe_name(name)
    with _lock:
        entries = _read_trust()
        existed = name in entries
        entries.pop(name, None)
        _write_trust(entries)
        _loaded.pop(name, None)
    return existed


def _timed(fn, *args):
    t0 = time.monotonic()
    result = fn(*args)
    elapsed = time.monotonic() - t0
    if elapsed > _SOFT_LIMIT_S:
        # This is telemetry, not a sandbox timeout.  Trusted Python executes in
        # process and can still block; the UI warning states that explicitly.
        logger.warning("user hook %s exceeded soft limit: %.1fs", getattr(fn, "__module__", "?"), elapsed)
    return result


def _register(name: str, mod: object, digest: str) -> None:
    """Adapt one already-trusted module into the deny-first system hook chain."""
    try:
        from hashmm.hooks import HookDecision, register_post_hook, register_pre_hook
    except Exception as exc:
        log_suppressed(logger, exc, "user_hooks.register_import")
        return

    key = (name, digest)
    if key in _registered:
        return
    _registered.add(key)

    pre = getattr(mod, "pre_tool", None)
    if callable(pre):
        def _pre(tool: str, args: dict, ctx: dict, _fn=pre, _n=name, _sha=digest) -> "HookDecision":
            if not _is_active(_n, _sha):
                return HookDecision(allow=True, hook=f"user:{_n}")
            try:
                # Deep copies are required: a shallow ``dict(args)`` still lets
                # a Hook mutate nested command/options after permission checks.
                result = _timed(_fn, tool, copy.deepcopy(args or {}), copy.deepcopy(ctx or {}))
                if isinstance(result, dict) and result.get("deny"):
                    return HookDecision(
                        allow=False,
                        reason=str(result.get("reason") or f"被用户 Hook {_n} 拒绝"),
                        risk="high",
                        hook=f"user:{_n}",
                    )
                if isinstance(result, dict) and isinstance(result.get("args"), dict):
                    logger.warning("user hook %s requested args rewrite; ignored pending permission re-check support", _n)
            except Exception as exc:
                log_suppressed(logger, exc, f"user_hooks.pre({_n})")
            return HookDecision(allow=True, hook=f"user:{_n}")
        register_pre_hook(f"user:{name}:{digest[:12]}", _pre, before="permission")

    post = getattr(mod, "post_tool", None)
    if callable(post):
        def _post(tool: str, args: dict, ctx: dict, ok: bool, latency_ms: int,
                  _fn=post, _n=name, _sha=digest) -> None:
            if not _is_active(_n, _sha):
                return
            try:
                _timed(
                    _fn, tool, copy.deepcopy(args or {}), copy.deepcopy(ctx or {}),
                    bool(ok), int(latency_ms),
                )
            except Exception as exc:
                log_suppressed(logger, exc, f"user_hooks.post({_n})")
        register_post_hook(f"user:{name}:{digest[:12]}", _post)


def load_all(force: bool = False) -> list[str]:
    """Load only exact, trusted hook snapshots.  Untrusted code is never imported."""
    global _load_done
    if not _enabled():
        return []
    with _lock:
        if _load_done and not force:
            return loaded_names()
        _load_done = True
        entries = _read_trust()
        d = _hooks_dir()
        if not d.is_dir():
            return []
        for path in sorted(d.glob("*.py"), key=lambda p: p.name.lower()):
            name = path.stem
            if name.startswith("_") or not _NAME_RE.fullmatch(name):
                continue
            try:
                data, _ = _read_snapshot(path)
                digest = _digest(data)
                trusted = str((entries.get(name) or {}).get("sha256") or "")
                if not trusted or not secrets.compare_digest(trusted, digest):
                    _loaded.pop(name, None)
                    continue
                if (_loaded.get(name) or {}).get("sha256") == digest:
                    continue
                code = compile(data, str(path), "exec")
                mod = types.ModuleType(f"hashmm_user_hook_{name}_{digest[:12]}")
                mod.__file__ = str(path)
                exec(code, mod.__dict__)  # noqa: S102 - exact digest was explicitly trusted
                _loaded[name] = {"module": mod, "sha256": digest, "source": str(path)}
                _register(name, mod, digest)
                logger.info("trusted user hook loaded: %s sha256=%s", path.name, digest)
            except Exception as exc:
                _loaded.pop(name, None)
                log_suppressed(logger, exc, f"user_hooks.load({path.name})")
        return loaded_names()


def run_on_finish(summary: dict, ctx: dict) -> None:
    """Run active Stop observers.  A changed/revoked hook is skipped immediately."""
    if not _enabled():
        return
    load_all()
    for name, record in list(_loaded.items()):
        digest = str(record.get("sha256") or "")
        if not digest or not _is_active(name, digest):
            continue
        mod = record.get("module")
        fn = getattr(mod, "on_finish", None)
        if callable(fn):
            try:
                _timed(fn, copy.deepcopy(summary or {}), copy.deepcopy(ctx or {}))
            except Exception as exc:
                log_suppressed(logger, exc, f"user_hooks.finish({name})")


def loaded_names() -> list[str]:
    return sorted(name for name, record in _loaded.items()
                  if _is_active(name, str(record.get("sha256") or "")))


def _reset_for_tests() -> None:
    """Reset process-local state; tests also reset ``hashmm.hooks`` registries."""
    global _load_done
    with _lock:
        _loaded.clear()
        _registered.clear()
        _load_done = False

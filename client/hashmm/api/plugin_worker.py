"""One-shot worker for an exact, administrator-approved Python plugin.

This is a crash, lifetime and Python-audit-policy boundary, not an operating-
system sandbox. The parent process still applies manifest trust, permissions,
approval and audit policy before invoking this worker. The protocol is one JSON request on stdin
and exactly one JSON response on stdout; plugin output is captured as data so
it cannot corrupt the protocol.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import io
import json
import os
import sys
from pathlib import Path
from typing import Any


def _apply_audit_policy(package_root: Path, permissions: dict[str, Any]) -> None:
    """Fail closed for the only in-process profile we can actually enforce."""
    if str(permissions.get("filesystem") or "none") != "none":
        raise RuntimeError("worker only supports filesystem=none; use MCP for broader access")
    if str(permissions.get("network") or "none") != "none":
        raise RuntimeError("worker only supports network=none; use MCP for broader access")

    allowed_roots = tuple(os.path.normcase(os.path.abspath(value)) for value in {
        str(package_root), sys.base_prefix, sys.exec_prefix,
    })
    mutation_events = {
        "os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir",
        "os.chmod", "os.chown", "os.truncate", "shutil.copyfile",
    }

    def within_allowed(value: object) -> bool:
        if isinstance(value, int):
            return True
        try:
            candidate = os.path.normcase(os.path.abspath(os.fspath(value)))
        except (TypeError, ValueError, OSError):
            return False
        return any(candidate == root or candidate.startswith(root + os.sep) for root in allowed_roots)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "pty.spawn"}:
            raise PermissionError("plugin network/process access denied")
        if event in mutation_events:
            raise PermissionError("plugin filesystem mutation denied")
        if event == "open":
            path = args[0] if args else ""
            mode = args[1] if len(args) > 1 else "r"
            flags = int(args[2] or 0) if len(args) > 2 else 0
            writes = (isinstance(mode, str) and any(ch in mode for ch in "wax+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if writes or not within_allowed(path):
                raise PermissionError("plugin file access denied")
        if event in {"os.listdir", "os.scandir"} and args and not within_allowed(args[0]):
            raise PermissionError("plugin directory access denied")

    sys.dont_write_bytecode = True
    sys.addaudithook(audit)


def _apply_resource_limits() -> None:
    if os.name == "nt":
        return
    try:
        import resource

        memory = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except Exception:
        # Some container runtimes disallow lowering one or more limits.  The
        # parent timeout and process boundary remain effective.
        pass


def _load(entrypoint: Path, package_root: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        entrypoint,
        submodule_search_locations=[str(package_root)] if entrypoint.name == "__init__.py" else None,
    )
    if not spec or not spec.loader:
        raise RuntimeError("无法创建插件模块加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    register = getattr(module, "register_tools", None)
    if not callable(register):
        raise RuntimeError("Python 插件必须导出 register_tools()")
    registered = register()
    if not isinstance(registered, list):
        raise RuntimeError("register_tools() 必须返回列表")
    return registered


def _finish(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def main() -> int:
    _apply_resource_limits()
    try:
        request = json.loads(sys.stdin.buffer.read() or b"{}")
        package_root = Path(str(request["package_root"])).resolve(strict=True)
        entrypoint = Path(str(request["entrypoint"])).resolve(strict=True)
        _apply_audit_policy(package_root, dict(request.get("permissions") or {}))
        if os.path.commonpath((str(entrypoint), str(package_root))) != str(package_root):
            raise RuntimeError("插件入口越界")
        module_name = "hashmm_plugin_worker_" + str(request.get("digest") or "")[:16]
        captured_out, captured_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            registered = _load(entrypoint, package_root, module_name)
            tools: dict[str, dict[str, Any]] = {}
            for raw in registered:
                if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not callable(raw.get("executor")):
                    raise RuntimeError("插件工具注册项无效")
                if raw["name"] in tools:
                    raise RuntimeError("插件注册了重复工具")
                tools[raw["name"]] = raw
            if request.get("action") == "describe":
                result: Any = {"tools": sorted(tools)}
            elif request.get("action") == "execute":
                name = str(request.get("tool") or "")
                if name not in tools:
                    raise RuntimeError("插件工具未注册")
                result = _finish(tools[name]["executor"](dict(request.get("args") or {}), dict(request.get("ctx") or {})))
            else:
                raise RuntimeError("未知插件工作请求")
        response = {"ok": True, "result": result}
        # Validate serialisability before touching the protocol stream.
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    except BaseException as exc:
        payload = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}, ensure_ascii=False, separators=(",", ":"))
    sys.__stdout__.write(payload)
    sys.__stdout__.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

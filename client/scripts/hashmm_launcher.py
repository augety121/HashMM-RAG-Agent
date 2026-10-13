#!/usr/bin/env python3
"""HashMM server launcher and deployment doctor.

This is the single source of truth behind the ``*.sh`` entry points.  It
loads an external .env file, validates the exact code/configuration that will
run, prevents duplicate instances, and only then starts uvicorn.  Secrets are
never printed and optional packages are never installed during normal start.
"""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
MIN_RELEASE = 1501
SECRET_NAMES = {
    "HASHMM_JWT_SECRET",
    "HASHMM_SECRET",
    "HASHMM_SUPABASE_SERVICE_KEY",
    "HASHMM_SERPER_API_KEY",
    "HASHMM_BENCH_INGEST_TOKEN",
    "LLM_API_KEY",
}
MIGRATABLE_ENV_NAMES = {
    # Server boundary and identity.  The migration command reads these as data;
    # it never sources or executes the legacy shell script.
    "HASHMM_HOST",
    "HASHMM_PORT",
    "HASHMM_ENV",
    "HASHMM_REQUIRE_AUTH",
    "HASHMM_IDENTITY_PROVIDER_REQUIRED",
    "HASHMM_CORS_ORIGINS",
    "HASHMM_PUBLIC_URL",
    "HASHMM_JWT_SECRET",
    "HASHMM_SECRET",
    "HASHMM_ACCESS_TTL",
    "HASHMM_SUPABASE_URL",
    "HASHMM_SUPABASE_PUBLISHABLE_KEY",
    "HASHMM_SUPABASE_SERVICE_KEY",
    "HASHMM_SUPABASE_ADMIN_EMAILS",
    # Optional capabilities that were historically stored in the launcher.
    "HASHMM_SERPER_API_KEY",
    "HASHMM_SEARCH_BACKEND",
    "HASHMM_BENCH_HOME",
    "HASHMM_BENCH_MODE",
    "HASHMM_BENCH_CONCURRENCY",
    "HASHMM_BENCH_INGEST_TOKEN",
}
ENV_ALIASES = {
    # Common Supabase names used by older web projects and deployment panels.
    # They are accepted only as configuration aliases and are normalized to
    # HashMM's canonical names before the application imports its settings.
    "HASHMM_SUPABASE_URL": (
        "SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_URL",
    ),
    "HASHMM_SUPABASE_PUBLISHABLE_KEY": (
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    ),
    "HASHMM_SUPABASE_SERVICE_KEY": (
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ),
}
LOCAL_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:6006",
    "http://127.0.0.1:6006",
    "http://localhost:17615",
    "http://127.0.0.1:17615",
)
PLACEHOLDER_PARTS = (
    "change-me", "changeme", "replace-me", "placeholder", "your_", "your-",
    "在此填", "换成", "粘贴", "占位", "示例", "service_role_secret",
)
OPTIONAL_GROUPS = {
    "stt": ["faster-whisper"],
    "docs": ["python-pptx", "python-docx", "openpyxl"],
    "video": ["yt-dlp"],
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _placeholder(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    low = text.lower()
    return any(part in low for part in PLACEHOLDER_PARTS)


def _release_number(value: str) -> int:
    match = re.fullmatch(r"V(\d+)", str(value or "").strip(), re.I)
    return int(match.group(1)) if match else -1


def _supabase_project_ref(url: str) -> str:
    """Extract a public Supabase project ref without logging credentials."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
        return host.split(".", 1)[0] if host.endswith(".supabase.co") else ""
    except Exception:
        return ""


def _jwt_project_ref(token: str) -> str:
    """Read only the public ``ref`` claim for configuration diagnostics.

    This never establishes identity and never returns or logs the credential.
    """
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return ""
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return str(claims.get("ref") or "").strip().lower()
    except Exception:
        return ""


def _stored_identity_override_count() -> int:
    """Count stale DB identity rows read-only; never return their values."""
    configured = os.environ.get("HASHMM_DB_PATH", "").strip()
    data_root = Path(os.environ.get("HASHMM_DATA_DIR", str(ROOT / "data"))).expanduser()
    path = Path(configured).expanduser() if configured else data_root / "hashmm.sqlite"
    if not path.is_file():
        return 0
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            placeholders = ",".join("?" for _ in range(3))
            row = connection.execute(
                f"SELECT COUNT(*) FROM app_settings WHERE key IN ({placeholders}) AND value <> ''",
                ("supabase_url", "supabase_publishable_key", "supabase_admin_emails"),
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.Error, ValueError):
        return 0


def _parse_legacy_env(path: Path) -> dict[str, str]:
    """Parse an allowlisted subset of ``export NAME=value`` without executing it.

    Old HashMM launchers contained credentials alongside operational defaults.
    Sourcing such a file during migration would also execute arbitrary shell
    commands (including ``pip install`` and here-documents), so migration must
    treat the file as untrusted text.
    """
    if not path.is_file():
        raise RuntimeError(f"旧启动脚本不存在：{path}")
    assignment = re.compile(
        r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
    )
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text("utf-8", errors="replace").splitlines(), 1):
        match = assignment.match(line)
        if not match or match.group(1) not in MIGRATABLE_ENV_NAMES:
            continue
        name, raw = match.group(1), match.group(2)
        try:
            parts = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeError(f"旧启动脚本第 {number} 行无法安全解析：{exc}") from exc
        if len(parts) != 1:
            raise RuntimeError(f"旧启动脚本第 {number} 行不是单一静态值，已拒绝迁移 {name}")
        value = parts[0].strip()
        # Shell expansion/substitution is intentionally unsupported.  A secret
        # migration must never execute data supplied by an old script.
        if not value or "$(" in value or "`" in value or value.startswith("${"):
            continue
        values[name] = value
    return values


def _dotenv_quote(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _read_dotenv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
        return {
            str(key): str(value)
            for key, value in dotenv_values(path).items()
            if key and value is not None
        }
    except Exception as exc:
        raise RuntimeError(f"无法读取现有配置文件 {path}：{exc}") from exc


def _resolved_env_path() -> Path:
    configured = os.environ.get("HASHMM_ENV_FILE", "").strip()
    env_file = Path(configured).expanduser() if configured else ROOT / ".env"
    if not env_file.is_absolute():
        env_file = ROOT / env_file
    return env_file.resolve()


def _effective_config_value(values: dict[str, str], name: str) -> str:
    inherited = os.environ.get(name, "").strip()
    if inherited and not _placeholder(inherited):
        return inherited
    current = str(values.get(name, "") or "").strip()
    if current and not _placeholder(current):
        return current
    for alias in ENV_ALIASES.get(name, ()):
        inherited_alias = os.environ.get(alias, "").strip()
        if inherited_alias and not _placeholder(inherited_alias):
            return inherited_alias
        file_alias = str(values.get(alias, "") or "").strip()
        if file_alias and not _placeholder(file_alias):
            return file_alias
    return inherited or current


def _default_cors_origins(values: dict[str, str]) -> str:
    origins = list(LOCAL_CORS_ORIGINS)
    public_url = _effective_config_value(values, "HASHMM_PUBLIC_URL")
    if public_url:
        parsed = urlparse(public_url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            origin = f"{parsed.scheme.lower()}://{parsed.netloc}"
            if origin not in origins:
                origins.append(origin)
    return ",".join(origins)


def _write_dotenv_atomic(path: Path, updates: dict[str, str], *, force: bool) -> list[str]:
    """Merge values into a dotenv file atomically and return changed key names."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_values = _read_dotenv_values(path)
    accepted: dict[str, str] = {}
    for name, value in updates.items():
        current = existing_values.get(name, "")
        if force or _placeholder(current):
            accepted[name] = value
    if not accepted:
        return []

    lines = path.read_text("utf-8", errors="replace").splitlines() if path.is_file() else []
    positions: dict[str, int] = {}
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for index, line in enumerate(lines):
        match = assignment.match(line)
        if match:
            positions[match.group(1)] = index
    for name, value in accepted.items():
        rendered = f"{name}={_dotenv_quote(value)}"
        if name in positions:
            lines[positions[name]] = rendered
        else:
            lines.append(rendered)

    payload = "\n".join(lines).rstrip() + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass
    return sorted(accepted)


def _bootstrap_env_file() -> tuple[Path | None, list[str]]:
    """Normalize legacy aliases and persist a safe public-server baseline.

    External identity values are never invented.  Independent server secrets
    can be generated locally and persisted safely, which avoids asking an
    operator to paste them into shell history.  Existing non-placeholder
    values and inherited process configuration always win.
    """
    path = _resolved_env_path()
    values = _read_dotenv_values(path)
    updates: dict[str, str] = {}

    for canonical in ENV_ALIASES:
        if _placeholder(_effective_config_value(values, canonical)):
            continue
        if _placeholder(values.get(canonical)) and not os.environ.get(canonical, "").strip():
            updates[canonical] = _effective_config_value(values, canonical)

    profile = os.environ.get("HASHMM_DEPLOYMENT_PROFILE", "").strip().lower()
    host = _effective_config_value(values, "HASHMM_HOST")
    public_url = _effective_config_value(values, "HASHMM_PUBLIC_URL")
    public = profile in {"autodl", "server", "production", "public"}
    public = public or (host and host.lower() not in {"127.0.0.1", "localhost", "::1"})
    public = public or bool(public_url)
    if public:
        for name in ("HASHMM_JWT_SECRET", "HASHMM_SECRET"):
            if _placeholder(_effective_config_value(values, name)):
                updates[name] = secrets.token_urlsafe(48)
        if _effective_config_value(values, "HASHMM_ENV").strip().lower() != "production":
            updates["HASHMM_ENV"] = "production"
        if not _truthy(_effective_config_value(values, "HASHMM_REQUIRE_AUTH")):
            updates["HASHMM_REQUIRE_AUTH"] = "1"
        cors = _effective_config_value(values, "HASHMM_CORS_ORIGINS")
        if not cors or "*" in {item.strip() for item in cors.split(",")}:
            updates["HASHMM_CORS_ORIGINS"] = _default_cors_origins(values)

    # Only explicitly selected insecure/placeholder keys are present in
    # ``updates``.  Force applies to that bounded set, allowing a copied
    # .env.example's HASHMM_ENV=dev / HASHMM_REQUIRE_AUTH=0 to be repaired.
    changed = _write_dotenv_atomic(path, updates, force=True) if updates else []
    if path.is_file() and os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise RuntimeError(f"无法把配置文件权限收紧为 0600：{exc}") from exc
    if changed:
        print("[start] 已补全安全配置变量：" + ", ".join(changed))
        print("[start] 随机密钥已直接写入 0600 配置文件，未输出到终端。")
    return (path if path.is_file() else None), changed


def _migrate_legacy_env(source: str, destination: str, *, force: bool) -> int:
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    destination_path = Path(destination).expanduser()
    if not destination_path.is_absolute():
        destination_path = ROOT / destination_path

    values = _parse_legacy_env(source_path.resolve())
    # Optional placeholder secrets in historical launchers meant "feature
    # disabled".  Persisting them makes the production doctor reject an
    # otherwise valid server, so omit them instead of inventing credentials.
    values = {
        name: value
        for name, value in values.items()
        if not (name in SECRET_NAMES and _placeholder(value))
    }
    # Legacy launchers exposed an AutoDL-mapped service without consistently
    # declaring the production boundary.  The migrated configuration must be
    # usable *and* fail closed; native desktop/App traffic is not subject to
    # browser CORS, while the embedded same-origin shell uses these localhost
    # origins.
    values.setdefault("HASHMM_ENV", "production")
    values.setdefault("HASHMM_REQUIRE_AUTH", "1")
    values.setdefault(
        "HASHMM_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:6006",
    )
    # HASHMM_SECRET did not exist in early launchers.  It is independent from
    # the JWT signing key and must not silently reuse that key.
    current = _read_dotenv_values(destination_path.resolve())
    if _placeholder(current.get("HASHMM_SECRET")) and "HASHMM_SECRET" not in values:
        values["HASHMM_SECRET"] = secrets.token_urlsafe(48)
    changed = _write_dotenv_atomic(destination_path, values, force=force)
    print(f"[start] 已安全迁移 {len(changed)} 项到 {destination_path.resolve()}")
    if changed:
        print("[start] 已更新变量：" + ", ".join(changed))
    else:
        print("[start] 现有非占位配置均已保留，没有需要覆盖的项目")
    print("[start] 未执行旧脚本，也未输出任何密钥值。现在请运行：./start-hashmm1.sh doctor")
    return 0


def _configured_identity_values(env_file: Path) -> dict[str, str]:
    """Return the effective identity boundary without exposing its values."""
    values = _read_dotenv_values(env_file)
    for name in (
        "HASHMM_JWT_SECRET",
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
        "HASHMM_SUPABASE_SERVICE_KEY",
        "HASHMM_SUPABASE_ADMIN_EMAILS",
    ):
        inherited = os.environ.get(name, "").strip()
        if inherited:
            values[name] = inherited
        elif _placeholder(values.get(name)):
            alias_value = _effective_config_value(values, name)
            if alias_value:
                values[name] = alias_value
    return values


def _auto_migrate_legacy_env() -> bool:
    """One-time, opt-in migration used by the AutoDL compatibility wrapper.

    The wrapper only supplies a path to a user-owned legacy launcher.  This
    function parses the allowlisted assignments as untrusted data, fills
    missing identity values, and never executes that launcher.  A complete
    existing identity boundary is left untouched.
    """
    source = os.environ.get("HASHMM_LEGACY_ENV_SCRIPT", "").strip()
    if not source or not _truthy(os.environ.get("HASHMM_AUTO_MIGRATE_LEGACY_ENV")):
        return False
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    if not source_path.is_file():
        return False

    env_file = _resolved_env_path()
    effective = _configured_identity_values(env_file.resolve())
    required = (
        "HASHMM_JWT_SECRET",
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
    )
    if all(not _placeholder(effective.get(name)) for name in required):
        return False
    _migrate_legacy_env(str(source_path.resolve()), str(env_file.resolve()), force=False)
    return True


def _load_env() -> Path | None:
    env_file = _resolved_env_path()
    if not env_file.is_file():
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except Exception as exc:
        raise RuntimeError(f"无法读取配置文件 {env_file}: {exc}") from exc
    return env_file


def _set_defaults() -> None:
    deployment_profile = os.environ.get("HASHMM_DEPLOYMENT_PROFILE", "").strip().lower()
    default_host = (
        "0.0.0.0"
        if deployment_profile in {"autodl", "server", "production", "public"}
        else "127.0.0.1"
    )
    defaults = {
        "HASHMM_SRC": str(ROOT),
        "HASH_INDEX_DIR": str(ROOT / "data" / "vector_index"),
        "HASHMM_BASE_MODEL": str(ROOT / "models" / "Qwen2.5-7B-Instruct"),
        "HASHMM_SEARCHR1_LORA": str(ROOT / "models" / "qwen2.5-7b-hashmm-final-v2"),
        "HASHMM_PRESET": "max",
        "HASHMM_AUDIT_TOOLS": "1",
        "HASHMM_AGENT_TRACE": "1",
        "HASHMM_ACCESS_TTL": "2592000",
        "HASHMM_HOST": default_host,
        "HASHMM_PORT": "6006",
        "HASHMM_THREADPOOL_MAX": "96",
        "HASHMM_LAYERED_MEMORY": "1",
        "HASHMM_CONTEXT_OFFLOAD": "1",
        "HASHMM_WARMUP": "0",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _is_public(host: str, public_url: str) -> bool:
    profile = os.environ.get("HASHMM_DEPLOYMENT_PROFILE", "").strip().lower()
    if profile in {"autodl", "server", "production", "public"}:
        return True
    if host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        return True
    if public_url:
        try:
            parsed = urlparse(public_url)
            return (parsed.hostname or "").lower() not in {"", "127.0.0.1", "localhost", "::1"}
        except Exception:
            return True
    return False


def _port_free(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    family = socket.AF_INET6 if ":" in probe_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((probe_host, port)) != 0
    except OSError:
        return False


def _script_secret_assignments() -> list[str]:
    issues: list[str] = []
    assignment = re.compile(
        r"^\s*(?:export\s+)?(" + "|".join(map(re.escape, sorted(SECRET_NAMES))) + r")\s*=\s*(.+?)\s*$"
    )
    for name in ("hashmm-start.sh", "start-hashmm.sh", "start-hashmm1.sh"):
        path = ROOT / name
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text("utf-8", errors="replace").splitlines(), 1):
            match = assignment.match(line)
            if not match:
                continue
            raw = match.group(2).split("#", 1)[0].strip().strip("'\"")
            if raw and not raw.startswith("${") and not _placeholder(raw):
                issues.append(f"{name}:{number} 把 {match.group(1)} 写成了明文")
    return issues


def collect_diagnostics(*, check_port: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    if sys.version_info < (3, 10):
        errors.append(f"Python {sys.version.split()[0]} 过旧，需要 ≥3.10")
    else:
        checks.append(f"Python {sys.version.split()[0]}")

    for package in ("fastapi", "uvicorn", "numpy", "multipart"):
        try:
            importlib.import_module(package)
        except Exception as exc:
            errors.append(f"核心依赖 {package} 无法导入：{exc}")
    if not any("核心依赖" in item for item in errors):
        checks.append("核心依赖可导入")

    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import hashmm
        source = Path(hashmm.__file__).resolve().parent
        expected = (ROOT / "hashmm").resolve()
        if source != expected:
            errors.append(f"加载到旧代码副本：实际 {source}，期望 {expected}")
        release = getattr(hashmm, "RELEASE", "")
        if _release_number(release) < MIN_RELEASE:
            errors.append(f"代码版本 {release or '未知'} 低于启动器要求 V{MIN_RELEASE}")
        else:
            checks.append(f"HashMM {release} · {source}")
    except Exception as exc:
        errors.append(f"HashMM 导入失败：{exc}")

    env_file = Path(os.environ["HASHMM_ENV_FILE_RESOLVED"]) if os.environ.get("HASHMM_ENV_FILE_RESOLVED") else None
    if env_file:
        checks.append(f"配置文件 {env_file}")
        if os.name != "nt":
            try:
                if env_file.stat().st_mode & 0o077:
                    warnings.append(f"{env_file} 可被组/其他用户读取；建议 chmod 600")
            except OSError as exc:
                warnings.append(f"无法检查 .env 权限：{exc}")
    else:
        warnings.append("未找到 .env；当前只使用进程环境变量和安全默认值")

    errors.extend(_script_secret_assignments())

    identity_required = os.environ.get("HASHMM_IDENTITY_PROVIDER_REQUIRED", "").strip().lower()
    supabase_url = os.environ.get("HASHMM_SUPABASE_URL", "").strip().rstrip("/")
    supabase_key = os.environ.get("HASHMM_SUPABASE_PUBLISHABLE_KEY", "").strip()
    supabase_partial = bool(
        supabase_url
        or supabase_key
        or os.environ.get("HASHMM_SUPABASE_SERVICE_KEY", "").strip()
        or os.environ.get("HASHMM_SUPABASE_ADMIN_EMAILS", "").strip()
    )
    if identity_required not in {"", "none", "supabase"}:
        errors.append("HASHMM_IDENTITY_PROVIDER_REQUIRED 仅支持 none 或 supabase")
    if identity_required == "supabase" or supabase_partial:
        identity_missing = False
        if not supabase_url:
            errors.append("统一账号已要求启用，但 .env 缺少 HASHMM_SUPABASE_URL")
            identity_missing = True
        elif urlparse(supabase_url).scheme.lower() != "https":
            errors.append("HASHMM_SUPABASE_URL 必须使用 HTTPS")
        if not supabase_key or _placeholder(supabase_key):
            errors.append("统一账号已要求启用，但 .env 缺少有效 HASHMM_SUPABASE_PUBLISHABLE_KEY")
            identity_missing = True
        if identity_missing:
            warnings.append(
                "Supabase 账号值不会被伪造：请保留/上传上一版私有 .env，"
                "或把旧脚本放到项目根后执行 "
                "./start-hashmm1.sh migrate-env --from-script 'start-hashmm1 (1).sh'"
            )
        if supabase_url and supabase_key and not _placeholder(supabase_key):
            project_ref = _supabase_project_ref(supabase_url)
            suffix = f" · project={project_ref}" if project_ref else ""
            checks.append(f"Supabase 统一身份配置完整{suffix} · source=environment（密钥值未输出）")
            service_ref = _jwt_project_ref(os.environ.get("HASHMM_SUPABASE_SERVICE_KEY", ""))
            if service_ref and project_ref and service_ref != project_ref:
                errors.append("HASHMM_SUPABASE_SERVICE_KEY 所属项目与 HASHMM_SUPABASE_URL 不一致")
            if os.environ.get("HASHMM_ENV", "").strip().lower() in {"prod", "production"}:
                override_count = _stored_identity_override_count()
                if override_count:
                    warnings.append(
                        f"检测到 {override_count} 条旧数据库身份配置；生产环境已忽略，.env 是唯一身份来源"
                    )

    host = os.environ.get("HASHMM_HOST", "127.0.0.1").strip()
    try:
        port = int(os.environ.get("HASHMM_PORT", "6006"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        port = 6006
        errors.append("HASHMM_PORT 必须是 1–65535 的整数")

    public_url = os.environ.get("HASHMM_PUBLIC_URL", "").strip()
    public = _is_public(host, public_url)
    production = os.environ.get("HASHMM_ENV", "").strip().lower() in {"prod", "production"}
    if public:
        required = {
            "HASHMM_JWT_SECRET": 32,
            "HASHMM_SECRET": 32,
        }
        for key, minimum in required.items():
            value = os.environ.get(key, "")
            if _placeholder(value) or len(value) < minimum:
                errors.append(f"公网监听时 {key} 必须是至少 {minimum} 位的非占位随机值")
        if not _truthy(os.environ.get("HASHMM_REQUIRE_AUTH")):
            errors.append("公网监听时必须设置 HASHMM_REQUIRE_AUTH=1")
        cors = os.environ.get("HASHMM_CORS_ORIGINS", "").strip()
        if not cors or "*" in {item.strip() for item in cors.split(",")}:
            errors.append("公网监听时必须用 HASHMM_CORS_ORIGINS 明确列出允许来源，不能留空或使用 *")
        if public_url and urlparse(public_url).scheme.lower() != "https":
            message = (
                "生产公网 HASHMM_PUBLIC_URL 必须使用 HTTPS；请改为真实的 https:// 域名，"
                "不能填写 http://公网IP。若当前只有校园网 HTTP，请删除 HASHMM_PUBLIC_URL，"
                "且不要把该入口当作公网服务"
            )
            (errors if production else warnings).append(message)
    checks.append(f"监听 {host}:{port}（{'公网/局域网' if public else '仅本机'}）")

    if public_url and not _truthy(os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE")):
        message = (
            "生产公网地址已配置，但 HASHMM_REQUIRE_SECURE_REMOTE 未启用；"
            "HTTPS 入口就绪后设置 HASHMM_REQUIRE_SECURE_REMOTE=1"
        )
        (errors if production else warnings).append(message)

    if _truthy(os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE")):
        if urlparse(public_url).scheme.lower() != "https":
            errors.append("HASHMM_REQUIRE_SECURE_REMOTE=1 时 HASHMM_PUBLIC_URL 必须使用 HTTPS")

    if _truthy(os.environ.get("HASHMM_REMOTE_REQUIRE_TURN")):
        turn_secret = os.environ.get("HASHMM_TURN_SHARED_SECRET", "")
        raw_turn_urls = os.environ.get("HASHMM_TURN_URLS", "")
        try:
            decoded_turn_urls = json.loads(raw_turn_urls)
            turn_urls = decoded_turn_urls if isinstance(decoded_turn_urls, list) else []
        except (TypeError, ValueError):
            turn_urls = raw_turn_urls.split(",")
        valid_turn_urls = [
            str(item).strip() for item in turn_urls
            if str(item).strip().startswith(("turn:", "turns:"))
        ]
        if len(turn_secret) < 32 or _placeholder(turn_secret):
            errors.append("HASHMM_REMOTE_REQUIRE_TURN=1 时 HASHMM_TURN_SHARED_SECRET 必须是至少 32 位的非占位随机值")
        if not valid_turn_urls:
            errors.append("HASHMM_REMOTE_REQUIRE_TURN=1 时 HASHMM_TURN_URLS 必须至少包含一个 turn: 或 turns: 地址")
        if valid_turn_urls and len(turn_secret) >= 32 and not _placeholder(turn_secret):
            checks.append("自有 TURN 动态短期凭证配置完整（密钥值未输出）")

    for key in ("HASHMM_SUPABASE_SERVICE_KEY", "HASHMM_SERPER_API_KEY", "HASHMM_BENCH_INGEST_TOKEN"):
        value = os.environ.get(key, "")
        if value and _placeholder(value):
            errors.append(f"{key} 仍是占位值；请填写真实值或删除该变量以禁用功能")

    index_dir = Path(os.environ["HASH_INDEX_DIR"]).expanduser()
    try:
        index_dir.mkdir(parents=True, exist_ok=True)
        probe = index_dir / ".hashmm-write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        checks.append(f"索引目录可写 {index_dir}")
        if not any(index_dir.iterdir()):
            warnings.append("向量索引目录为空；RAG 在完成首批入库前不会有可引用证据")
    except OSError as exc:
        errors.append(f"索引目录不可写 {index_dir}：{exc}")

    for key, label in (("HASHMM_BASE_MODEL", "基础模型"), ("HASHMM_SEARCHR1_LORA", "SearchR1 LoRA")):
        path = Path(os.environ.get(key, "")).expanduser()
        if not path.is_dir():
            warnings.append(f"{label}目录不存在：{path}；相关能力会降级，不会伪报已启用")

    if check_port and not _port_free(host, port):
        errors.append(f"端口 {host}:{port} 已被占用；请先停止旧进程或修改 HASHMM_PORT")

    return {
        "ok": not errors,
        "release_required": f"V{MIN_RELEASE}",
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "config": {
            "host": host,
            "port": port,
            "preset": os.environ.get("HASHMM_PRESET", "max"),
            "index_dir": str(index_dir),
            "public": public,
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    release = "HashMM"
    try:
        source = (ROOT / "hashmm" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'^RELEASE\s*=\s*["\'](V\d+)["\']', source, re.MULTILINE)
        if match:
            release = match.group(1)
    except OSError:
        pass
    print(f"[start] ── {release} 启动诊断 ─────────────────────────")
    for item in report["checks"]:
        print(f"[start] OK   {item}")
    for item in report["warnings"]:
        print(f"[start] WARN {item}")
    for item in report["errors"]:
        print(f"[start] FAIL {item}")
    print("[start] ─────────────────────────────────────────")


def _lock(port: int):
    run_dir = Path(os.environ.get("HASHMM_RUN_DIR", str(ROOT / "data" / "run"))).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)
    handle = (run_dir / f"hashmm-{port}.lock").open("a+", encoding="ascii")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as exc:
        handle.close()
        raise RuntimeError(f"已有 HashMM 启动器持有端口 {port} 的运行锁") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _install_optional(group: str) -> int:
    names = list(OPTIONAL_GROUPS) if group == "all" else [group]
    packages = [package for name in names for package in OPTIONAL_GROUPS[name]]
    print("[start] 将安装可选能力：" + ", ".join(packages))
    return subprocess.call([sys.executable, "-m", "pip", "install", *packages])


def _serve(report: dict[str, Any]) -> int:
    port = int(report["config"]["port"])
    # Keep a process-global reference; otherwise CPython may close the file and release the lock.
    globals()["_SERVER_LOCK"] = _lock(port)

    if os.environ.get("HASHMM_WARMUP", "0") == "1":
        import threading
        def warm() -> None:
            try:
                from hashmm.retrieval import searchr1_serving
                searchr1_serving.get_policy()
                print("[start] warmup complete", flush=True)
            except Exception as exc:
                print(f"[start] warmup degraded: {exc}", flush=True)
        threading.Timer(8.0, warm).start()

    import uvicorn
    print(f"[start] starting HashMM on {report['config']['host']}:{port}", flush=True)
    uvicorn.run(
        "hashmm.api.server:app",
        host=report["config"]["host"],
        port=port,
        log_level=os.environ.get("HASHMM_LOG_LEVEL", "info").lower(),
        proxy_headers=_truthy(os.environ.get("HASHMM_PROXY_HEADERS")),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HashMM secure launcher")
    sub = parser.add_subparsers(dest="command")
    doctor = sub.add_parser("doctor", help="只检查，不启动")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-port-check", action="store_true")
    install = sub.add_parser("install-optional", help="显式安装可选能力；正常启动不会联网安装")
    install.add_argument("group", choices=[*OPTIONAL_GROUPS, "all"], nargs="?", default="all")
    migrate = sub.add_parser("migrate-env", help="从旧启动脚本安全迁移配置到 .env（不执行旧脚本）")
    migrate.add_argument("--from-script", required=True, help="旧启动脚本路径")
    migrate.add_argument("--env-file", default=".env", help="目标 dotenv 文件（默认项目根 .env）")
    migrate.add_argument("--force", action="store_true", help="覆盖目标文件中已有的非占位值")
    sub.add_parser("start", help="检查通过后启动（默认）")
    args = parser.parse_args(argv)
    command = args.command or "start"

    os.chdir(ROOT)
    if command == "migrate-env":
        try:
            return _migrate_legacy_env(args.from_script, args.env_file, force=args.force)
        except RuntimeError as exc:
            print(f"[start] FAIL {exc}", file=sys.stderr)
            return 2
    try:
        _auto_migrate_legacy_env()
    except RuntimeError as exc:
        print(f"[start] FAIL 无法安全迁移旧启动配置：{exc}", file=sys.stderr)
        return 2
    try:
        _bootstrap_env_file()
    except RuntimeError as exc:
        print(f"[start] FAIL 无法初始化安全配置：{exc}", file=sys.stderr)
        return 2
    try:
        env_file = _load_env()
    except RuntimeError as exc:
        print(f"[start] FAIL {exc}", file=sys.stderr)
        return 2
    if env_file:
        os.environ["HASHMM_ENV_FILE_RESOLVED"] = str(env_file)
    _set_defaults()

    if command == "install-optional":
        return _install_optional(args.group)

    report = collect_diagnostics(check_port=not getattr(args, "no_port_check", False))
    if command == "doctor" and args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    if not report["ok"]:
        print("[start] 诊断未通过，服务未启动。修正 FAIL 项后重试。", file=sys.stderr)
        return 2
    return 0 if command == "doctor" else _serve(report)


if __name__ == "__main__":
    raise SystemExit(main())

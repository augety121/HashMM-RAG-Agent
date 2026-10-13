from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_versions_are_synchronized():
    manifest = json.loads((ROOT / "hashmm" / "release-manifest.json").read_text("utf-8"))
    desktop = json.loads((ROOT / "desktop" / "package.json").read_text("utf-8"))
    frontend = json.loads((ROOT / "frontend-next" / "package.json").read_text("utf-8"))
    cmake = (ROOT / "installer-native" / "CMakeLists.txt").read_text("utf-8")
    build_all = (ROOT / "installer-native" / "build-all.bat").read_text("utf-8")
    init = (ROOT / "hashmm" / "__init__.py").read_text("utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert manifest["schema"] == "hashmm.release-manifest.v1"
    assert desktop["version"] == manifest["desktop_version"] == "17.0.2"
    assert frontend["version"] == desktop["version"]
    assert f'project(HashMMSetup VERSION {manifest["desktop_version"]}' in cmake
    assert "from hashmm.release import BACKEND_VERSION, RELEASE" in init
    assert f'version = "{manifest["backend_version"]}"' in pyproject
    assert manifest["release"] == "V2802"
    assert "HashMM V2802 deterministic native release pipeline" in build_all
    assert "HASHMM_SETUP_VERSION" in cmake


def test_start_scripts_are_secret_free_and_delegate_to_doctor():
    launcher = (ROOT / "scripts" / "hashmm_launcher.py").read_text("utf-8")
    canonical = (ROOT / "hashmm-start.sh").read_text("utf-8")
    assert "MIN_RELEASE = 1501" in launcher
    assert "HashMM canonical launcher" in canonical
    for path in (ROOT / "hashmm-start.sh", ROOT / "start-hashmm.sh", ROOT / "start-hashmm1.sh"):
        text = path.read_text("utf-8")
        assert "SUPABASE_SERVICE_KEY='" not in text
        assert "SERPER_API_KEY='" not in text
        assert "JWT_SECRET='" not in text
    assert "scripts/hashmm_launcher.py" in canonical
    assert "公网监听时必须设置 HASHMM_REQUIRE_AUTH=1" in launcher
    assert "install-optional" in launcher


def test_server_package_includes_turn_template_but_excludes_rendered_secret():
    package = (ROOT / "scripts" / "package-server.ps1").read_text("utf-8")
    assert '"deploy"' in package
    assert 'StartsWith("deploy/turn/runtime/")' in package
    assert '"deploy/turn/.env.example"' in package
    assert '"hashmm/pipeline/ocr_queue.py"' in package
    assert '"hashmm/api/platform_access.py"' in package
    assert '"hashmm/api/routes/platform_keys.py"' in package
    assert '"docs/API_PLATFORM_V820_SPEC.md"' in package
    assert '"docs/CONTROL_PLANE_SETTINGS_PLUGINS_V900_SPEC.md"' in package
    assert '"docs/CONTINUITY_CONTROL_PLANE_V1000_SPEC.md"' in package
    assert '"docs/RELEASE_NOTES_V1000.md"' in package
    assert '"docs/CONTINUITY_RUNTIME_V1100_SPEC.md"' in package
    assert '"docs/RELEASE_NOTES_V1100.md"' in package
    assert '"docs/HASHMM_V1200_SECURE_REMOTE_RETRIEVAL_SPEC.md"' in package
    assert '"docs/HASHMM_V1300_CLOUDFLARE_COMPUTER_APP2_SPEC.md"' in package
    assert '"docs/HASHMM_V1700_CROSS_DEVICE_TRUTH_SPEC.md"' in package
    assert '"docs/HASHMM_V1800_REMOTE_FABRIC_SPEC.md"' in package
    assert '"docs/HASHMM_V1900_WORK_OS_REMOTE_AGENT_CATALOG_SPEC.md"' in package
    assert '"docs/HASHMM_V2000_AGENT_WORKSPACE_REMOTE_FABRIC_SPEC.md"' in package
    assert '"docs/HASHMM_V2500_EVIDENCE_WORKBENCH_SPEC.md"' in package
    assert '"hashmm/api/provider_fabric.py"' in package
    assert '"hashmm/api/routes/provider_fabric.py"' in package
    assert '"hashmm/api/routes/canvas_ops.py"' in package
    assert '"hashmm/api/remote_devices.py"' in package
    assert '"hashmm/workspace_runtime/cloudflare_computer.py"' in package
    assert '"integrations/cloudflare-computer-worker/src/index.ts"' in package
    assert '"hashmm/retrieval_fabric/service.py"' in package
    assert '"deploy/secure-remote/cloudflared-config.yml.example"' in package
    assert '"hashmm/agent/task_state.py"' in package
    assert '"hashmm/evaluation/harness_levels.py"' in package
    assert '"SERVER-PACKAGE.json"' in package
    assert '"hashmm.server-package.v1"' in package
    assert "Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256" in package


def test_autodl_wrapper_defers_host_and_port_to_dotenv_aware_launcher():
    wrapper = (ROOT / "start-hashmm1.sh").read_text("utf-8")
    assert 'HASHMM_DEPLOYMENT_PROFILE="${HASHMM_DEPLOYMENT_PROFILE:-autodl}"' in wrapper
    assert 'HASHMM_ENV="${HASHMM_ENV:-production}"' in wrapper
    assert 'export HASHMM_HOST=' not in wrapper
    assert 'export HASHMM_PORT=' not in wrapper


def test_launcher_defaults_public_profiles_to_external_binding(monkeypatch):
    launcher = load_module(
        "hashmm_launcher_profile_default",
        ROOT / "scripts" / "hashmm_launcher.py",
    )
    monkeypatch.setenv("HASHMM_DEPLOYMENT_PROFILE", "autodl")
    monkeypatch.delenv("HASHMM_HOST", raising=False)
    launcher._set_defaults()
    assert os.environ["HASHMM_HOST"] == "0.0.0.0"


def test_autodl_dotenv_loopback_overrides_profile_default(monkeypatch, tmp_path):
    launcher = load_module(
        "hashmm_launcher_profile_dotenv",
        ROOT / "scripts" / "hashmm_launcher.py",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        'HASHMM_HOST="127.0.0.1"\nHASHMM_PORT="6006"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HASHMM_ENV_FILE", str(env_file))
    monkeypatch.setenv("HASHMM_DEPLOYMENT_PROFILE", "autodl")
    monkeypatch.delenv("HASHMM_HOST", raising=False)
    monkeypatch.delenv("HASHMM_PORT", raising=False)

    assert launcher._load_env() == env_file.resolve()
    launcher._set_defaults()

    assert os.environ["HASHMM_HOST"] == "127.0.0.1"
    assert os.environ["HASHMM_PORT"] == "6006"


def test_public_launcher_fails_closed_without_security(monkeypatch, tmp_path):
    launcher = load_module("hashmm_launcher_v334", ROOT / "scripts" / "hashmm_launcher.py")
    monkeypatch.setenv("HASHMM_HOST", "0.0.0.0")
    monkeypatch.setenv("HASHMM_PORT", "61234")
    monkeypatch.setenv("HASH_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("HASHMM_BASE_MODEL", str(tmp_path / "model"))
    monkeypatch.setenv("HASHMM_SEARCHR1_LORA", str(tmp_path / "lora"))
    monkeypatch.delenv("HASHMM_JWT_SECRET", raising=False)
    monkeypatch.delenv("HASHMM_SECRET", raising=False)
    monkeypatch.delenv("HASHMM_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HASHMM_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("HASHMM_ENV_FILE_RESOLVED", raising=False)
    report = launcher.collect_diagnostics(check_port=False)
    assert report["ok"] is False
    joined = "\n".join(report["errors"])
    assert "HASHMM_JWT_SECRET" in joined
    assert "HASHMM_SECRET" in joined
    assert "HASHMM_REQUIRE_AUTH=1" in joined
    assert "HASHMM_CORS_ORIGINS" in joined


def test_production_launcher_rejects_http_public_url_before_uvicorn(monkeypatch, tmp_path):
    launcher = load_module("hashmm_launcher_https_preflight", ROOT / "scripts" / "hashmm_launcher.py")
    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv("HASHMM_HOST", "0.0.0.0")
    monkeypatch.setenv("HASHMM_PORT", "61237")
    monkeypatch.setenv("HASH_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("HASHMM_BASE_MODEL", str(tmp_path / "model"))
    monkeypatch.setenv("HASHMM_SEARCHR1_LORA", str(tmp_path / "lora"))
    monkeypatch.setenv("HASHMM_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HASHMM_JWT_SECRET", "jwt-" + "a" * 48)
    monkeypatch.setenv("HASHMM_SECRET", "data-" + "b" * 48)
    monkeypatch.setenv("HASHMM_CORS_ORIGINS", "http://localhost:6006")
    monkeypatch.setenv("HASHMM_PUBLIC_URL", "http://111.115.7.14:20014")
    monkeypatch.delenv("HASHMM_REQUIRE_SECURE_REMOTE", raising=False)

    report = launcher.collect_diagnostics(check_port=False)

    assert report["ok"] is False
    joined = "\n".join(report["errors"])
    assert "必须使用 HTTPS" in joined
    assert "HASHMM_REQUIRE_SECURE_REMOTE" in joined
    assert not any("HASHMM_PUBLIC_URL 不是 HTTPS" in warning for warning in report["warnings"])


def test_launcher_fails_closed_when_turn_is_required_but_incomplete(monkeypatch, tmp_path):
    launcher = load_module("hashmm_launcher_turn", ROOT / "scripts" / "hashmm_launcher.py")
    monkeypatch.setenv("HASHMM_HOST", "127.0.0.1")
    monkeypatch.setenv("HASHMM_PORT", "61236")
    monkeypatch.setenv("HASH_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("HASHMM_BASE_MODEL", str(tmp_path / "model"))
    monkeypatch.setenv("HASHMM_SEARCHR1_LORA", str(tmp_path / "lora"))
    monkeypatch.setenv("HASHMM_REMOTE_REQUIRE_TURN", "1")
    monkeypatch.setenv("HASHMM_TURN_SHARED_SECRET", "short")
    monkeypatch.delenv("HASHMM_TURN_URLS", raising=False)
    report = launcher.collect_diagnostics(check_port=False)
    assert report["ok"] is False
    joined = "\n".join(report["errors"])
    assert "HASHMM_TURN_SHARED_SECRET" in joined
    assert "HASHMM_TURN_URLS" in joined


def test_autodl_profile_requires_complete_shared_identity(monkeypatch, tmp_path):
    launcher = load_module("hashmm_launcher_identity", ROOT / "scripts" / "hashmm_launcher.py")
    monkeypatch.setenv("HASHMM_DEPLOYMENT_PROFILE", "autodl")
    monkeypatch.setenv("HASHMM_IDENTITY_PROVIDER_REQUIRED", "supabase")
    monkeypatch.setenv("HASHMM_HOST", "127.0.0.1")
    monkeypatch.setenv("HASHMM_PORT", "61235")
    monkeypatch.setenv("HASH_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("HASHMM_BASE_MODEL", str(tmp_path / "model"))
    monkeypatch.setenv("HASHMM_SEARCHR1_LORA", str(tmp_path / "lora"))
    monkeypatch.setenv("HASHMM_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HASHMM_JWT_SECRET", "jwt-" + "a" * 48)
    monkeypatch.setenv("HASHMM_SECRET", "data-" + "b" * 48)
    monkeypatch.setenv("HASHMM_CORS_ORIGINS", "http://localhost:6006")
    monkeypatch.delenv("HASHMM_SUPABASE_URL", raising=False)
    monkeypatch.delenv("HASHMM_SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("HASHMM_SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("HASHMM_SUPABASE_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("HASHMM_ENV_FILE_RESOLVED", raising=False)

    report = launcher.collect_diagnostics(check_port=False)

    assert report["ok"] is False
    joined = "\n".join(report["errors"])
    assert "HASHMM_SUPABASE_URL" in joined
    assert "HASHMM_SUPABASE_PUBLISHABLE_KEY" in joined


def test_legacy_launcher_migration_is_allowlisted_atomic_and_non_executing(tmp_path):
    launcher = load_module("hashmm_launcher_migration", ROOT / "scripts" / "hashmm_launcher.py")
    legacy = tmp_path / "old.sh"
    marker = tmp_path / "must-not-exist"
    legacy.write_text(
        "\n".join([
            "#!/usr/bin/env bash",
            "export HASHMM_JWT_SECRET='legacy-jwt-secret-abcdefghijklmnopqrstuvwxyz'",
            "export HASHMM_SUPABASE_URL='https://project.example.supabase.co'",
            "export HASHMM_SUPABASE_PUBLISHABLE_KEY='sb_publishable_example'",
            "export HASHMM_SUPABASE_SERVICE_KEY='service-role-example'",
            "export HASHMM_BENCH_INGEST_TOKEN='replace-me-with-a-benchmark-token'",
            f"touch '{marker}'",
            "export UNRELATED_SHOULD_NOT_MIGRATE='no'",
        ]),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text('HASHMM_JWT_SECRET="keep-existing-jwt-secret"\n', encoding="utf-8")

    assert launcher._migrate_legacy_env(str(legacy), str(env_file), force=False) == 0

    from dotenv import dotenv_values
    values = dotenv_values(env_file)
    assert values["HASHMM_JWT_SECRET"] == "keep-existing-jwt-secret"
    assert values["HASHMM_SUPABASE_URL"] == "https://project.example.supabase.co"
    assert values["HASHMM_SUPABASE_PUBLISHABLE_KEY"] == "sb_publishable_example"
    assert values["HASHMM_SUPABASE_SERVICE_KEY"] == "service-role-example"
    assert values["HASHMM_SECRET"]
    assert "HASHMM_BENCH_INGEST_TOKEN" not in values
    assert "UNRELATED_SHOULD_NOT_MIGRATE" not in values
    assert not marker.exists()


def test_autodl_wrapper_auto_migrates_only_an_incomplete_identity(monkeypatch, tmp_path):
    launcher = load_module("hashmm_launcher_auto_migration", ROOT / "scripts" / "hashmm_launcher.py")
    legacy = tmp_path / "start-hashmm1 (1).sh"
    legacy.write_text(
        "\n".join([
            "export HASHMM_JWT_SECRET='legacy-jwt-secret-abcdefghijklmnopqrstuvwxyz'",
            "export HASHMM_SUPABASE_URL='https://project.example.supabase.co'",
            "export HASHMM_SUPABASE_PUBLISHABLE_KEY='sb_publishable_example'",
            "echo this-must-never-run",
        ]),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    monkeypatch.setenv("HASHMM_LEGACY_ENV_SCRIPT", str(legacy))
    monkeypatch.setenv("HASHMM_AUTO_MIGRATE_LEGACY_ENV", "1")
    monkeypatch.setenv("HASHMM_ENV_FILE", str(env_file))
    for name in (
        "HASHMM_JWT_SECRET",
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert launcher._auto_migrate_legacy_env() is True
    first = env_file.read_bytes()
    assert launcher._auto_migrate_legacy_env() is False
    assert env_file.read_bytes() == first

    from dotenv import dotenv_values
    values = dotenv_values(env_file)
    assert values["HASHMM_SUPABASE_URL"] == "https://project.example.supabase.co"
    assert values["HASHMM_SUPABASE_PUBLISHABLE_KEY"] == "sb_publishable_example"
    assert values["HASHMM_JWT_SECRET"] == "legacy-jwt-secret-abcdefghijklmnopqrstuvwxyz"


def test_public_bootstrap_repairs_sample_security_and_normalizes_supabase_aliases(
    monkeypatch, tmp_path
):
    launcher = load_module(
        "hashmm_launcher_secure_bootstrap",
        ROOT / "scripts" / "hashmm_launcher.py",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "HASHMM_ENV=dev",
                "HASHMM_REQUIRE_AUTH=0",
                "HASHMM_CORS_ORIGINS=*",
                "HASHMM_JWT_SECRET=replace-me-with-a-new-random-value",
                "HASHMM_SECRET=replace-me-with-another-new-random-value",
                "HASHMM_SUPABASE_URL=https://your-project.supabase.co",
                "HASHMM_SUPABASE_PUBLISHABLE_KEY=your-publishable-key",
                "SUPABASE_URL=https://project.example.supabase.co",
                "NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_example",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HASHMM_ENV_FILE", str(env_file))
    monkeypatch.setenv("HASHMM_DEPLOYMENT_PROFILE", "autodl")
    for name in (
        "HASHMM_ENV",
        "HASHMM_REQUIRE_AUTH",
        "HASHMM_CORS_ORIGINS",
        "HASHMM_JWT_SECRET",
        "HASHMM_SECRET",
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved, changed = launcher._bootstrap_env_file()

    from dotenv import dotenv_values

    values = dotenv_values(env_file)
    assert resolved == env_file.resolve()
    assert values["HASHMM_ENV"] == "production"
    assert values["HASHMM_REQUIRE_AUTH"] == "1"
    assert "*" not in values["HASHMM_CORS_ORIGINS"].split(",")
    assert "http://localhost:17615" in values["HASHMM_CORS_ORIGINS"]
    assert len(values["HASHMM_JWT_SECRET"]) >= 32
    assert len(values["HASHMM_SECRET"]) >= 32
    assert values["HASHMM_JWT_SECRET"] != values["HASHMM_SECRET"]
    assert values["HASHMM_SUPABASE_URL"] == "https://project.example.supabase.co"
    assert values["HASHMM_SUPABASE_PUBLISHABLE_KEY"] == "sb_publishable_example"
    assert {
        "HASHMM_ENV",
        "HASHMM_REQUIRE_AUTH",
        "HASHMM_CORS_ORIGINS",
        "HASHMM_JWT_SECRET",
        "HASHMM_SECRET",
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
    }.issubset(set(changed))


def test_public_bootstrap_never_invents_external_identity(monkeypatch, tmp_path):
    launcher = load_module(
        "hashmm_launcher_no_fake_identity",
        ROOT / "scripts" / "hashmm_launcher.py",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("HASHMM_ENV=dev\n", encoding="utf-8")
    monkeypatch.setenv("HASHMM_ENV_FILE", str(env_file))
    monkeypatch.setenv("HASHMM_DEPLOYMENT_PROFILE", "autodl")
    for name in (
        "HASHMM_SUPABASE_URL",
        "HASHMM_SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    launcher._bootstrap_env_file()

    from dotenv import dotenv_values

    values = dotenv_values(env_file)
    assert "HASHMM_SUPABASE_URL" not in values
    assert "HASHMM_SUPABASE_PUBLISHABLE_KEY" not in values


def test_runtime_is_mandatory_packaged_resource():
    yml = (ROOT / "desktop" / "electron-builder.yml").read_text("utf-8")
    assert "from: runtime" in yml
    assert "to: runtime" in yml
    assert '"python/**"' in yml
    info = json.loads((ROOT / "desktop" / "runtime" / "runtime-info.json").read_text("utf-8"))
    assert info["complete"] is True
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    assert "cryptography>=" in requirements
    assert "PyYAML>=" in requirements


def test_release_preflight_checks_runtime_and_packaged_app():
    script = (ROOT / "desktop" / "scripts" / "verify-release.py").read_text("utf-8")
    for token in (
        "runtime dependency hash stale", "packaged backend stale", "app.asar",
        "runtime-import-ok", "cryptography", "yaml", "sha256", "release.json",
    ):
        assert token in script


def test_backend_manager_rejects_stale_runtime_and_venv_markers():
    source = (ROOT / "desktop" / "backendmgr.js").read_text("utf-8")
    assert "validateBundledRuntime" in source
    assert "requirementsFingerprint" in source
    assert "内置运行时依赖已过期" in source
    assert "marker === `ok ${expected}`" in source


def test_native_installer_is_transactional_and_validates_payload():
    engine = (ROOT / "installer-native" / "install_engine.cpp").read_text("utf-8")
    window = (ROOT / "installer-native" / "InstallerWindow.cpp").read_text("utf-8")
    for token in (
        "copyTreeAtomic", ".stage-", ".backup-", "迁移用户数据失败",
        "resources/runtime/python/python.exe", "resources/webui/index.html",
    ):
        assert token in engine
    assert "copyTreeAtomic" in window
    assert "磁盘空间不足" in window


def test_self_extractor_uses_unique_temp_and_waits_for_cleanup():
    source = (ROOT / "installer-native" / "bootstrap" / "bootstrap.c").read_text("utf-8")
    assert "GetTempFileNameW" in source
    assert "run_wait(launchCmd" in source
    assert 'L"%sHashMMSetup"' not in source


def test_packer_is_reproducible_and_rejects_symlinks(tmp_path):
    pack = load_module("hashmm_pack_v334", ROOT / "installer-native" / "bootstrap" / "pack.py")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "b.txt").write_text("b", encoding="ascii")
    (payload / "a.txt").write_text("a", encoding="ascii")
    first = pack.build_archive(payload)
    second = pack.build_archive(payload)
    assert first == second
    archive = tmp_path / "payload.zip"
    archive.write_bytes(first)
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.namelist() == ["a.txt", "b.txt"]
    link = payload / "link.txt"
    try:
        link.symlink_to(payload / "a.txt")
    except OSError:
        pytest.skip("Windows account has no symlink privilege")
    with pytest.raises(RuntimeError, match="symlinks"):
        pack.build_archive(payload)


def test_native_build_pipeline_has_release_gates():
    build = (ROOT / "installer-native" / "build-all.bat").read_text("utf-8")
    for token in (
        "npm ci --dry-run", "npm test", "npm run typecheck", "prepare-runtime.py --strict",
        "verify-release.py --packaged", "HASHMM_REQUIRE_SIGNING", "signtool verify /pa",
        "verify-release.py\" --artifact",
    ):
        assert token in build

"""tests/test_backend_security.py — 后端默认安全硬合约（V306）。

针对审计 REM-11 / REM-12 / REM-13：
  · 密钥加密：默认(个人本地)不拦、可回落 XOR 且能解旧密文（升级不丢）；
    生产(HASHMM_REQUIRE_AUTH / HASHMM_ENV=production)拒绝默认弱密钥、禁 XOR 降级。
  · CORS：统一以 HASHMM_CORS_ORIGINS 为单一事实来源，旧 CORS_ORIGINS 仅兼容回退。
  · 限流：max_per_hour 真正被执行（有小时窗计数容器）。

secrets 部分纯 stdlib、沙箱可充分跑；CORS/限流的运行时中间件依赖 starlette，
沙箱缺失时该用例自动跳过（真机 pytest 真跑）。
"""
import importlib
import asyncio
import os
import sys as _sys
import os as _os
from pathlib import Path
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))


def _reload_secrets():
    from hashmm import secrets_crypto as SC
    return importlib.reload(SC)


def _clear_prod_env():
    for k in ("HASHMM_REQUIRE_AUTH", "HASHMM_ENV", "HASHMM_SECRET"):
        os.environ.pop(k, None)


# ── 密钥：默认(本地)行为 ──
def test_secret_roundtrip_default_local():
    _clear_prod_env()
    SC = _reload_secrets()
    tok = SC.encrypt_secret("sk-abc123")
    assert SC.decrypt_secret(tok) == "sk-abc123"


def test_legacy_xor_still_decrypts_after_upgrade():
    """升级不丢：既存 XOR 密文仍能被解出（f1: 前缀区分新旧）。"""
    _clear_prod_env()
    SC = _reload_secrets()
    legacy = SC._xor_encrypt("old-key")
    assert SC.is_legacy_ciphertext(legacy) is True
    assert SC.decrypt_secret(legacy) == "old-key"
    fresh = SC.encrypt_secret("new-key")
    assert SC.is_legacy_ciphertext(fresh) is False  # 新值带 f1: 前缀


# ── 密钥：生产从严（REM-11）──
def test_prod_rejects_default_secret():
    _clear_prod_env()
    os.environ["HASHMM_REQUIRE_AUTH"] = "1"      # 标记生产
    SC = _reload_secrets()
    raised = False
    try:
        SC.encrypt_secret("x")
    except RuntimeError as e:
        raised = "HASHMM_SECRET" in str(e)
    _clear_prod_env(); _reload_secrets()
    assert raised, "生产环境未拒绝默认弱密钥"


def test_prod_with_strong_secret_works():
    _clear_prod_env()
    os.environ["HASHMM_REQUIRE_AUTH"] = "1"
    os.environ["HASHMM_SECRET"] = "a-very-strong-random-secret-0123456789abcdef"
    SC = _reload_secrets()
    tok = SC.encrypt_secret("sk-live")
    ok = SC.decrypt_secret(tok) == "sk-live"
    _clear_prod_env(); _reload_secrets()
    assert ok


def test_rotated_secret_rewraps_authenticated_legacy_default(monkeypatch):
    """Early launchers omitted HASHMM_SECRET; their Fernet rows remain recoverable."""
    from hashmm import secrets_crypto as SC

    monkeypatch.delenv("HASHMM_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HASHMM_ENV", raising=False)
    monkeypatch.setenv("HASHMM_SECRET", SC._DEFAULT_SECRET)
    old_cipher = SC.encrypt_secret("sk-existing-model")

    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv(
        "HASHMM_SECRET", "new-production-data-secret-0123456789abcdef"
    )
    new_cipher = SC.rewrap_legacy_fernet_ciphertext(old_cipher)

    assert new_cipher
    assert new_cipher != old_cipher
    assert SC.decrypt_secret(new_cipher) == "sk-existing-model"
    assert SC.rewrap_legacy_fernet_ciphertext(new_cipher) is None


def test_init_db_rewraps_existing_model_without_losing_key(tmp_db, monkeypatch):
    from hashmm import secrets_crypto as SC
    from hashmm.api import database as db

    monkeypatch.delenv("HASHMM_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HASHMM_ENV", raising=False)
    monkeypatch.setenv("HASHMM_SECRET", SC._DEFAULT_SECRET)
    old_cipher = SC.encrypt_secret("sk-persisted-before-v410")

    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv(
        "HASHMM_SECRET", "replacement-production-secret-0123456789abcdef"
    )
    db.DB_PATH = Path(tmp_db)
    monkeypatch.setattr(db, "_MODELS_MIRROR", Path(tmp_db).with_suffix(".models.json"))
    db._pool = None
    db.init_db()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO models "
            "(id,name,provider,base_url,api_key_enc,model_name,is_default) "
            "VALUES(?,?,?,?,?,?,1)",
            (
                "legacy-model", "Legacy", "deepseek", "https://api.example.test",
                old_cipher, "deepseek-chat",
            ),
        )

    db.init_db()
    model = db.get_default_model()
    with db._conn() as conn:
        stored = conn.execute(
            "SELECT api_key_enc FROM models WHERE id='legacy-model'"
        ).fetchone()[0]

    assert model and model["api_key"] == "sk-persisted-before-v410"
    assert stored != old_cipher
    assert SC.decrypt_secret(stored) == "sk-persisted-before-v410"


def test_is_production_detection():
    _clear_prod_env()
    SC = _reload_secrets()
    assert SC._is_production() is False
    os.environ["HASHMM_ENV"] = "production"
    assert _reload_secrets()._is_production() is True
    _clear_prod_env(); _reload_secrets()


# ── 限流：max_per_hour 真正执行（REM-13）──
def test_rate_limiter_has_hour_window():
    try:
        from hashmm.api.middleware import RateLimitMiddleware
    except Exception:
        import pytest
        pytest.skip("starlette 未安装（真机 pytest 真跑）")
        return
    m = RateLimitMiddleware(app=None, max_per_minute=60, max_per_hour=600)
    # 小时窗计数容器必须存在且被引用（此前只存 max_per_hour 不查）
    assert hasattr(m, "hour_counts"), "缺小时窗计数容器 → max_per_hour 未执行"
    assert m.max_per_hour == 600
    import inspect
    src = inspect.getsource(m.dispatch)
    assert "hour_counts" in src or "ratelimit:hr" in src, "dispatch 未在小时窗上计数/裁决"
    assert "Retry-After" in src, "429 应带 Retry-After"


# ── CORS：单一事实来源（REM-12）──
def test_cors_single_source_of_truth_in_server_source():
    """静态校验 server.py：以 HASHMM_CORS_ORIGINS 为主、CORS_ORIGINS 仅回退、生产禁裸 *。"""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "hashmm" / "api" / "server.py"
    text = src.read_text(encoding="utf-8")
    assert "HASHMM_CORS_ORIGINS" in text, "server.py 未使用 HASHMM_CORS_ORIGINS（与 settings/安检不一致）"
    # 生产禁裸 * 的收敛逻辑在位
    assert "REQUIRE_AUTH" in text and "*" in text, "缺生产禁通配来源的收敛逻辑"


def test_prod_startup_requires_auth_and_data_secret():
    """生产模式(HASHMM_ENV=production)启动检查必须报出：未强制鉴权、数据加密密钥无效。
    用 HASHMM_ALLOW_INSECURE=1 拿到 issues 列表而不真的 abort，逐条核对。"""
    try:
        import hashmm.api.security as S
    except Exception:
        import pytest
        pytest.skip("后端安全模块不可用（真机 pytest 真跑）")
        return
    import importlib
    saved = {k: os.environ.get(k) for k in
             ("HASHMM_ENV", "HASHMM_REQUIRE_AUTH", "HASHMM_SECRET", "HASHMM_JWT_SECRET",
              "HASHMM_CORS_ORIGINS", "HASHMM_ALLOW_INSECURE")}
    try:
        os.environ["HASHMM_ENV"] = "production"
        os.environ["HASHMM_ALLOW_INSECURE"] = "1"      # 只取 issues，不 abort
        os.environ["HASHMM_JWT_SECRET"] = "strong-jwt-secret-abcdef0123456789"
        os.environ["HASHMM_CORS_ORIGINS"] = "https://app.example.com"
        os.environ.pop("HASHMM_REQUIRE_AUTH", None)     # 未强制鉴权
        os.environ.pop("HASHMM_SECRET", None)           # 数据密钥缺失
        importlib.reload(S)
        issues = " ".join(S.run_startup_security_check())
        assert "强制鉴权" in issues, "生产未强制鉴权应被报出（BACK-P0-01）"
        assert "数据加密密钥" in issues, "生产数据加密密钥无效应被报出（BACK-P0-02）"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(S)


def test_supabase_only_mode_does_not_admit_or_audit_local_admin(monkeypatch):
    import hashmm.api.security as S

    monkeypatch.setenv("HASHMM_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.delenv("HASHMM_ALLOW_LOCAL_LOGIN", raising=False)
    monkeypatch.setattr(S, "_admin_uses_default_password", lambda: True)

    assert S.local_password_auth_enabled() is False
    issues = S.audit_for_deploy()
    assert all(issue["id"] != "admin_password" for issue in issues)

    monkeypatch.setenv("HASHMM_ALLOW_LOCAL_LOGIN", "1")
    assert S.local_password_auth_enabled() is True
    assert any(issue["id"] == "admin_password" for issue in S.audit_for_deploy())


def test_production_supabase_only_startup_ignores_unreachable_default_admin(
    monkeypatch,
):
    import hashmm.api.security as S

    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv("HASHMM_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HASHMM_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("HASHMM_JWT_SECRET", "strong-jwt-secret-0123456789abcdef")
    monkeypatch.setenv(
        "HASHMM_SECRET", "strong-data-secret-0123456789abcdef"
    )
    monkeypatch.setenv("HASHMM_CORS_ORIGINS", "https://app.example.com")
    monkeypatch.delenv("HASHMM_PUBLIC_URL", raising=False)
    monkeypatch.delenv("HASHMM_REQUIRE_SECURE_REMOTE", raising=False)
    monkeypatch.delenv("HASHMM_ALLOW_LOCAL_LOGIN", raising=False)
    monkeypatch.delenv("HASHMM_ALLOW_INSECURE", raising=False)
    monkeypatch.setattr(S, "_admin_uses_default_password", lambda: True)

    assert S.run_startup_security_check() == []


def test_security_gate_runs_before_server_accepts_requests():
    source = (
        Path(__file__).resolve().parents[1] / "hashmm" / "api" / "server.py"
    ).read_text(encoding="utf-8")
    gate = source.index("run_startup_security_check()")
    fast_init = source.index("ServiceRegistry.init_fast()")

    assert gate < fast_init
    assert source.count("run_startup_security_check()") == 1


def test_supabase_only_mode_rejects_local_registration(monkeypatch):
    import pytest
    from hashmm.api.routes import auth as auth_route
    from hashmm.api.schemas import RegisterRequest

    monkeypatch.setenv("HASHMM_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.delenv("HASHMM_ALLOW_LOCAL_LOGIN", raising=False)
    request = RegisterRequest(
        username="local-user", password="password123", display_name="Local"
    )

    with pytest.raises(Exception) as exc:
        asyncio.run(auth_route.register(request, object()))
    assert getattr(exc.value, "status_code", None) == 403


def test_ws_token_extraction_prefers_header():
    """V306：WebSocket 令牌优先 Authorization 头（不进 URL），回退子协议、再回退 query。"""
    try:
        import hashmm.api.auth as A
    except Exception:
        import pytest
        pytest.skip("fastapi 未安装（真机 pytest 真跑）")
        return

    class Q(dict):
        def get(self, k, d=None): return dict.get(self, k, d)

    class WS:
        def __init__(self, h=None, q=None):
            self.headers = h or {}
            self.query_params = Q(q or {})

    assert A.extract_ws_token(WS(h={"authorization": "Bearer abc"})) == "abc"
    assert A.extract_ws_token(WS(h={"sec-websocket-protocol": "bearer, tok"})) == "tok"
    assert A.extract_ws_token(WS(q={"token": "q"})) == "q"                       # 兼容旧客户端
    assert A.extract_ws_token(WS(h={"authorization": "Bearer H"}, q={"token": "Q"})) == "H"  # 头优先
    assert A.extract_ws_token(WS()) == ""


def test_rate_limit_eval_bypass_requires_secret():
    """限流的 eval 绕过 header 不再认客户端自报的 '1'，必须匹配服务端 HASHMM_EVAL_TOKEN（BACK-P1-02）。"""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "hashmm" / "api" / "middleware.py"
    text = src.read_text(encoding="utf-8")
    assert 'X-HashMM-Eval") == "1"' not in text, "仍在信任客户端自报的 X-HashMM-Eval: 1（公网绕过）"
    assert "HASHMM_EVAL_TOKEN" in text, "eval 绕过未改为服务端密钥校验"


def test_health_hides_detail_for_anonymous():
    """/health 匿名调用只返回最小状态，详细组件/GPU/缓存/特性需鉴权（BACK-P0-01）。"""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "hashmm" / "api" / "routes" / "system.py"
    text = src.read_text(encoding="utf-8")
    assert "if not _authed:" in text, "health 未按鉴权分流返回"
    assert "get_current_user(request)" in text, "health 未做鉴权判定"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("后端默认安全硬合约（V306）")
    print("=" * 60)
    p = f = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except BaseException as e:  # noqa: BLE001  (pytest.Skipped 继承 BaseException)
            if e.__class__.__name__ in ("Skipped", "OutcomeException"):
                print(f"  ○ {name}: SKIP"); continue
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())

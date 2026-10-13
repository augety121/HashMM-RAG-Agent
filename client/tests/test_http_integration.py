"""tests/test_http_integration.py — 后端真实 HTTP 集成测试（V306）。

用 FastAPI TestClient 起一个挂了**真实中间件（RateLimitMiddleware / CORS）+ 真实鉴权依赖
（require_auth / require_admin）** 的最小 app，发真实 HTTP 请求，端到端验证：
  · 无 token → 401；带有效 token → 200；
  · 管理员路由被普通用户访问 → 403；
  · 限流：短时间超过每分钟上限 → 429（带 Retry-After）；
  · CORS：白名单内 Origin 放行、白名单外不回显；
  · 错误码：不存在的路由 → 404，未捕获异常 → 500（且不泄栈）。

不加载 HashMM 全量 app（那需要一堆 ML 重依赖），只挂真实中间件 + 真实鉴权 + 少量测试路由——
所以测的是**真实的鉴权/限流/CORS 代码**，不是桩。fastapi 未装时跳过（CI 真跑）。
"""
import os
import sys as _sys, os as _os
import tempfile
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))

_os.environ.setdefault("HASHMM_DATA_DIR", tempfile.mkdtemp())


def _skip_if_no_fastapi():
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
        return False
    except Exception:
        import pytest
        pytest.skip("fastapi 未安装（CI 真跑）")
        return True


def _build_app_and_tokens():
    from fastapi import FastAPI, Depends, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from hashmm.api.middleware import RateLimitMiddleware
    from hashmm.api.auth import require_auth, require_admin
    from hashmm.api import database as db, auth as A

    app = FastAPI()
    # 真实限流中间件（小上限便于测 429）
    app.add_middleware(RateLimitMiddleware, max_per_minute=5, max_per_hour=1000)
    # 真实 CORS（白名单）
    app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.com"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/public")
    def public():
        return {"ok": True}

    @app.get("/api/private")
    def private(user=Depends(require_auth)):
        return {"ok": True, "uid": user.get("uid")}

    @app.get("/api/admin")
    def admin(user=Depends(require_admin)):
        return {"ok": True, "role": user.get("role")}

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("intentional-unhandled-error-secret")

    # 真实用户 + 真实 token（token_version 需用户存在才校验通过）
    # V308：建用户前必须先建表。此前缺这一步 → sqlite「no such table: users」。
    # 真机启动会走 init_db；测试用独立临时 DB，需自行初始化 schema。
    db.init_db()
    import uuid
    su = "user_" + uuid.uuid4().hex[:6]
    sa = "admin_" + uuid.uuid4().hex[:6]
    u = db.create_user(su, "pw12345678", "U", "user")
    a = db.create_user(sa, "pw12345678", "A", "admin")
    user_tok = A.create_token(u["id"], su, "user")
    admin_tok = A.create_token(a["id"], sa, "admin")
    return app, user_tok, admin_tok


def test_no_token_401():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/private")
    assert r.status_code == 401, f"无 token 应 401，实际 {r.status_code}"


def test_valid_token_200():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, user_tok, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/private", headers={"Authorization": f"Bearer {user_tok}"})
    assert r.status_code == 200, f"有效 token 应 200，实际 {r.status_code}: {r.text[:200]}"
    assert r.json().get("ok") is True


def test_bad_token_401():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/private", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401, f"伪造 token 应 401，实际 {r.status_code}"


def test_non_admin_forbidden_403():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, user_tok, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/admin", headers={"Authorization": f"Bearer {user_tok}"})
    assert r.status_code == 403, f"普通用户访问管理员路由应 403，实际 {r.status_code}"


def test_admin_allowed_200():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, admin_tok = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/admin", headers={"Authorization": f"Bearer {admin_tok}"})
    assert r.status_code == 200 and r.json().get("role") == "admin"


def test_rate_limit_429():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    # max_per_minute=5：前 5 个应过，之后 429
    codes = [c.get("/api/public").status_code for _ in range(9)]
    assert 429 in codes, f"超过每分钟上限未触发 429：{codes}"
    # 429 响应应带 Retry-After
    last = c.get("/api/public")
    if last.status_code == 429:
        assert "retry-after" in {k.lower() for k in last.headers}, "429 未带 Retry-After"


def test_cors_whitelist():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r1 = c.get("/api/public", headers={"Origin": "https://app.example.com"})
    assert r1.headers.get("access-control-allow-origin") == "https://app.example.com"
    r2 = c.get("/api/public", headers={"Origin": "https://evil.com"})
    assert r2.headers.get("access-control-allow-origin") not in ("https://evil.com", "*")


def test_404_for_unknown_route():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/does-not-exist").status_code == 404


def test_500_does_not_leak_stack():
    if _skip_if_no_fastapi():
        return
    from fastapi.testclient import TestClient
    app, _, _ = _build_app_and_tokens()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/boom")
    assert r.status_code == 500
    # 不应把内部异常明细/栈泄露给客户端
    assert "intentional-unhandled-error-secret" not in r.text, "500 响应泄露了内部异常明细"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("后端真实 HTTP 集成测试（V306：真中间件 + 真鉴权）")
    print("=" * 60)
    p = f = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except BaseException as e:  # noqa: BLE001
            if e.__class__.__name__ in ("Skipped", "OutcomeException"):
                print(f"  ○ {name}: SKIP"); continue
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())

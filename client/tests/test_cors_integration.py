"""tests/test_cors_integration.py — CORS 真实 Origin 集成测试（V306，补 BACK-P1-01）。

用最小 Starlette app 复现 HashMM 的 CORS 配置口径（HASHMM_CORS_ORIGINS 白名单 + 生产禁裸 *），
真实发带 Origin 头的请求，断言：
  · 白名单内 Origin → 响应带 Access-Control-Allow-Origin；
  · 白名单外 Origin → 不回该头（浏览器据此拦截）；
  · 预检 OPTIONS → 正确响应；
  · 生产(HASHMM_REQUIRE_AUTH)下配 "*" → 收敛为具体来源、绝不回 "*"。

不依赖 HashMM 全量 app（那需要一堆重依赖），只测中间件行为。starlette 未装时跳过（CI 真跑）。
"""
import os
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))


def _skip_if_no_starlette():
    try:
        import starlette  # noqa: F401
        from starlette.testclient import TestClient  # noqa: F401
        return None
    except Exception:
        import pytest
        pytest.skip("starlette 未安装（CI 真跑）")
        return "skip"


def _build_app(cors_origins_env: str, require_auth: bool):
    """按 server.py 的口径构造 CORS 中间件配置，挂到最小 app 上。"""
    from starlette.applications import Starlette
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    # —— 复刻 server.py 的解析逻辑 ——
    raw = cors_origins_env or "http://localhost:3000,http://localhost:6006"
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    prod = require_auth
    if prod and ("*" in origins or not origins):
        origins = [o for o in origins if o and o != "*"] or ["http://localhost:3000"]

    async def ping(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/ping", ping, methods=["GET", "POST"])])
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    return app, origins


def test_cors_allows_whitelisted_origin():
    if _skip_if_no_starlette():
        return
    from starlette.testclient import TestClient
    app, _ = _build_app("https://app.example.com,https://admin.example.com", require_auth=False)
    c = TestClient(app)
    r = c.get("/api/ping", headers={"Origin": "https://app.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com", \
        "白名单内 Origin 未获 CORS 放行"


def test_cors_blocks_unlisted_origin():
    if _skip_if_no_starlette():
        return
    from starlette.testclient import TestClient
    app, _ = _build_app("https://app.example.com", require_auth=False)
    c = TestClient(app)
    r = c.get("/api/ping", headers={"Origin": "https://evil.com"})
    # 非白名单：不应回显 evil.com（Starlette 对未命中 origin 不加该头）
    assert r.headers.get("access-control-allow-origin") not in ("https://evil.com", "*"), \
        "白名单外 Origin 被放行（CORS 失效）"


def test_cors_preflight_options():
    if _skip_if_no_starlette():
        return
    from starlette.testclient import TestClient
    app, _ = _build_app("https://app.example.com", require_auth=False)
    c = TestClient(app)
    r = c.options("/api/ping", headers={
        "Origin": "https://app.example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_cors_production_refuses_wildcard():
    if _skip_if_no_starlette():
        return
    from starlette.testclient import TestClient
    # 生产 + 配 "*" → 收敛为具体来源，绝不回 "*"
    app, origins = _build_app("*", require_auth=True)
    assert "*" not in origins, "生产模式未收敛通配来源"
    c = TestClient(app)
    r = c.get("/api/ping", headers={"Origin": "https://whatever.com"})
    assert r.headers.get("access-control-allow-origin") != "*", "生产仍回显 '*'（带凭证时危险）"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 56)
    print("CORS 真实 Origin 集成测试（V306）")
    print("=" * 56)
    p = f = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except BaseException as e:  # noqa: BLE001
            if e.__class__.__name__ in ("Skipped", "OutcomeException"):
                print(f"  ○ {name}: SKIP"); continue
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print("=" * 56)
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())

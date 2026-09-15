"""tests/test_local_semantic.py — V98 本地语义客户端回归（铁律 7：动主链先钉死）。

覆盖：默认关 = 零行为变化（identity 钉子）、假服务下重排按余弦生效且不丢不增、
服务 5xx → 原样返回并进入熔断冷却（第二次不再发请求）、坏 JSON 降级、
单结果直通。env 修改一律 try/finally 手动还原（铁律 8）。
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from hashmm import local_semantic as LS


class _R:
    """极简 SearchResult 替身：主链只依赖 .text 读 / .score 写。"""

    def __init__(self, text, score=0.0):
        self.text = text
        self.score = score

    def __repr__(self):  # pragma: no cover - 仅断言失败时可读
        return f"_R({self.text!r}, {self.score})"


class _FakeEmbed(BaseHTTPRequestHandler):
    calls = 0
    mode = "ok"  # ok | http500 | badjson

    def do_POST(self):
        type(self).calls += 1
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if type(self).mode == "http500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if type(self).mode == "badjson":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not json at all")
            return
        try:
            texts = (json.loads(raw) or {}).get("texts") or []
        except Exception:
            texts = []
        # 确定性向量：含"苹果"与 query 同向，其余正交 → 余弦可控
        vecs = [[1.0, 0.0] if "苹果" in t else [0.0, 1.0] for t in texts]
        body = json.dumps({"ok": True, "dim": 2, "vectors": vecs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _FakeEmbed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/local/embed"


def _with_env(url):
    """返回 (apply, restore)：env 设置与 try/finally 还原配套。"""
    old = os.environ.get(LS.ENV_KEY)

    def apply():
        if url is None:
            os.environ.pop(LS.ENV_KEY, None)
        else:
            os.environ[LS.ENV_KEY] = url
        LS._reset_for_tests()

    def restore():
        if old is None:
            os.environ.pop(LS.ENV_KEY, None)
        else:
            os.environ[LS.ENV_KEY] = old
        LS._reset_for_tests()

    return apply, restore


def test_default_off_is_identity():
    """env 未设 → 原列表对象原序返回 + available() False（默认关钉子）。"""
    apply, restore = _with_env(None)
    apply()
    try:
        rs = [_R("a"), _R("b"), _R("c")]
        out = LS.maybe_local_rerank("任意查询", rs, 2)
        assert out is rs, "默认关必须返回同一列表对象"
        assert [r.text for r in out] == ["a", "b", "c"]
        assert [r.score for r in out] == [0.0, 0.0, 0.0], "默认关不得回写 score"
        assert LS.available() is False
        assert LS.embed_texts(["x"]) is None
    finally:
        restore()


def test_rerank_reorders_by_cosine():
    """假服务：语义命中（含'苹果'）被顶到首位，集合不丢不增，单次合并请求。"""
    srv, url = _serve()
    apply, restore = _with_env(url)
    apply()
    _FakeEmbed.mode = "ok"
    _FakeEmbed.calls = 0
    try:
        rs = [_R("香蕉简介"), _R("橙子价格走势"), _R("苹果发布会纪要")]
        out = LS.maybe_local_rerank("苹果", rs, top_k=2)
        assert out is not rs, "生效时应返回新列表（identity 判定用）"
        assert out[0].text == "苹果发布会纪要", f"余弦应把语义命中顶上来，实际: {out}"
        assert sorted(r.text for r in out) == sorted(r.text for r in rs)
        assert out[0].score >= out[1].score >= out[2].score >= 0.0, "重排头部分数应单调"
        assert _FakeEmbed.calls == 1, "query+候选必须合并为一次请求"
    finally:
        restore()
        srv.shutdown()


def test_server_error_degrades_with_cooldown():
    """服务 500 → 原样返回 + 进入熔断；冷却期内第二次调用不再发请求。"""
    srv, url = _serve()
    apply, restore = _with_env(url)
    apply()
    _FakeEmbed.mode = "http500"
    _FakeEmbed.calls = 0
    try:
        rs = [_R("x"), _R("y")]
        out = LS.maybe_local_rerank("q", rs, 2)
        assert out is rs, "失败必须原样返回"
        assert _FakeEmbed.calls == 1
        assert LS.available() is False, "失败后应处于熔断冷却"
        out2 = LS.maybe_local_rerank("q", rs, 2)
        assert out2 is rs and _FakeEmbed.calls == 1, "冷却期内不得再发请求"
    finally:
        restore()
        srv.shutdown()
        _FakeEmbed.mode = "ok"


def test_bad_json_degrades():
    """200 但响应体不是 JSON → 原样返回并熔断，不抛错。"""
    srv, url = _serve()
    apply, restore = _with_env(url)
    apply()
    _FakeEmbed.mode = "badjson"
    _FakeEmbed.calls = 0
    try:
        rs = [_R("一"), _R("二")]
        out = LS.maybe_local_rerank("q", rs, 2)
        assert out is rs
        assert LS.available() is False
    finally:
        restore()
        srv.shutdown()
        _FakeEmbed.mode = "ok"


def test_single_result_passthrough():
    """≤1 个候选：直通不发请求（没有重排意义）。"""
    srv, url = _serve()
    apply, restore = _with_env(url)
    apply()
    _FakeEmbed.mode = "ok"
    _FakeEmbed.calls = 0
    try:
        rs = [_R("唯一结果")]
        out = LS.maybe_local_rerank("q", rs, 5)
        assert out is rs
        assert _FakeEmbed.calls == 0, "单候选不应触网"
        assert LS.maybe_local_rerank("", rs + [_R("x")], 5) is not None  # 空 query 也不炸
    finally:
        restore()
        srv.shutdown()

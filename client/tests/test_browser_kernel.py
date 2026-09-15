"""浏览器内核（V309）回归测试。

不依赖网络或已安装的 Chromium —— 覆盖纯逻辑面：SSRF 导航策略、lite 引擎 HTML 解析、
元素编号选择、快照渲染、内核会话池外观、优雅降级。Chromium 的真实渲染能力在
`check_env.sh`/联测里覆盖（需要 playwright），此处只保证策略与降级永不崩。
"""
import os

import pytest

from hashmm.tools import browser_kernel as bk


# ── SSRF / 导航策略 ──

@pytest.fixture(autouse=True)
def _no_private(monkeypatch):
    monkeypatch.delenv("HASHMM_BROWSER_ALLOW_PRIVATE", raising=False)
    # 策略单测不能依赖测试机当时的 DNS/网络。域名统一解析到文档公网段；
    # IP 字面量仍走真实 SSRF 分类逻辑，不受此 mock 影响。
    from hashmm.tools import net_guard
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port: [
        (net_guard.socket.AF_INET, net_guard.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
    ])


@pytest.mark.parametrize("url,allowed", [
    ("http://127.0.0.1/x", False),
    ("http://localhost:6006/", False),
    ("http://169.254.169.254/latest/meta-data/", False),   # 云元数据
    ("http://10.0.0.5/", False),
    ("http://192.168.1.1/", False),
    ("http://172.16.0.1/", False),
    ("http://2130706433/", False),                          # 十进制 127.0.0.1
    ("file:///etc/passwd", False),
    ("javascript:alert(1)", False),
    ("data:text/html,<h1>x", False),
    ("https://example.com/ok", True),
    ("https://arxiv.org/abs/1234.5678", True),
])
def test_nav_policy(url, allowed):
    ok, _why = bk.check_nav_allowed(url)
    assert ok is allowed


def test_nav_empty():
    ok, why = bk.check_nav_allowed("")
    assert ok is False and "空" in why


def test_allow_private_opens_local(monkeypatch):
    monkeypatch.setenv("HASHMM_BROWSER_ALLOW_PRIVATE", "1")
    ok, _ = bk.check_nav_allowed("http://127.0.0.1:6006/")
    assert ok is True
    # 但仍只允许 http/https
    ok2, _ = bk.check_nav_allowed("file:///etc/passwd")
    assert ok2 is False


# ── lite 引擎 HTML 解析 ──

_HTML = """<!doctype html><html><head><title>标题T</title>
<style>.x{color:red}</style><script>var a=1</script></head>
<body><h1>大标题</h1><p>正文内容ABC</p>
<a href="next.html">下一页</a><a href="https://ext.example/x">外链</a>
<a href="javascript:void(0)">脚本链接</a></body></html>"""


def test_lite_parse_title_and_text():
    p = bk._LitePage()
    p.feed(_HTML)
    assert p.title.strip() == "标题T"          # head 不被跳过（title 是其子元素）
    text = p.text()
    assert "正文内容ABC" in text and "大标题" in text
    assert "var a=1" not in text and "color:red" not in text   # script/style 被剔


def test_lite_parse_links_skip_js():
    p = bk._LitePage()
    p.feed(_HTML)
    hrefs = [h for _, h in p.links]
    assert "next.html" in hrefs and "https://ext.example/x" in hrefs
    assert not any(h.startswith("javascript:") for h in hrefs)   # js 链接不入表


# ── 元素编号选择 ──

def test_pick_element_by_number_and_text():
    els = [
        {"n": 1, "kind": "link", "text": "下一页", "href": "http://x/next"},
        {"n": 2, "kind": "button", "text": "提交表单", "href": ""},
    ]
    assert bk._pick_element(els, "1")["text"] == "下一页"
    assert bk._pick_element(els, "提交")["n"] == 2          # 文本兜底匹配
    assert bk._pick_element(els, "999") is None
    assert bk._pick_element(els, "") is None


# ── 快照渲染 ──

def test_snapshot_render_and_cap():
    snap = bk._Snapshot(url="https://a.test/p", title="页面X",
                        text="Z" * 5000,
                        elements=[{"n": 1, "kind": "link", "text": "点我", "href": "https://a.test/y"}])
    out = snap.render(cap=100)
    assert "页面X" in out and "https://a.test/p" in out
    assert "正文截断" in out                                 # 超 cap 截断标记
    assert "[1]" in out and "点我" in out


# ── lite 会话动作降级 ──

def test_lite_session_fill_press_degrade():
    s = bk._LiteSession()
    s._snap = bk._Snapshot(url="https://a.test", title="t", text="x",
                           elements=[{"n": 1, "kind": "link", "text": "l", "href": "https://a.test/2"}])
    for act in ("fill", "press", "scroll", "fill_form"):
        r = s.act(act)
        assert isinstance(r, str) and "playwright" in r.lower()   # 明确提示需要 playwright


def test_lite_wait_returns_snapshot():
    """lite 引擎的 wait 直接返回当前快照（无异步渲染），不报错。"""
    s = bk._LiteSession()
    s._snap = bk._Snapshot(url="https://a.test", title="T", text="hello", elements=[])
    r = s.act("wait")
    assert isinstance(r, str) and "hello" in r


def test_lite_read_tables_falls_back_to_text():
    s = bk._LiteSession()
    s._snap = bk._Snapshot(url="https://a.test", title="T", text="表格内容在正文里", elements=[])
    r = s.read("tables")
    assert "表格内容在正文里" in r   # 有兜底，不静默失败


def test_lite_click_requires_href():
    s = bk._LiteSession()
    s._snap = bk._Snapshot(url="https://a.test", title="t", text="x",
                           elements=[{"n": 1, "kind": "button", "text": "b", "href": ""}])
    r = s.act("click", target="1")
    assert isinstance(r, str) and "Error" in r                   # 无 href 不可点


# ── 内核外观：无会话时的动作全部安全报错，不抛异常 ──

def test_kernel_actions_without_open():
    k = bk.BrowserKernel()
    assert k.act("no-conv", "click", "1").startswith("Error")
    assert k.read("no-conv").startswith("Error")
    assert "still" not in k.act("no-conv", "click", "1")


def test_kernel_open_blocks_ssrf():
    k = bk.BrowserKernel()
    r = k.open("c1", "http://169.254.169.254/latest/")
    assert r.startswith("Error") and "拦截" in r


def test_kernel_stats():
    k = bk.BrowserKernel()
    st = k.stats()
    assert "active" in st and "engine" in st
    assert st["engine"] in ("chromium", "lite")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

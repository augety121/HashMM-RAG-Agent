"""hashmm/tools/browser_kernel.py — 浏览器内核（V309 新增）。

对标 Claude Code 桌面版内置浏览器 / Codex 的 Browser Use：给 agent 一个**有状态**的
真实浏览器会话——打开页面、点击、填表、翻页、截图——而不是 fetch_url 那种一次性抓文本。
WebVoyager/WebArena 这类真实网页任务、以及需要 JS 渲染的站点（SPA/表格/登录页）都靠它。

双引擎设计（能力优先，永不硬崩）：
  · playwright 引擎：Chromium 无头渲染，支持 JS/点击/填表/截图。需要
    `pip install playwright && playwright install chromium`（requirements-optional.txt）。
  · lite 引擎：纯 stdlib（urllib + html.parser）+ net_guard.safe_get 逐跳校验。
    没装 playwright 时自动降级：open/read/链接点击可用，填表/截图明确报"需要 playwright"。

线程模型：Playwright 同步 API **线程亲和**（对象只能在创建它的线程用）。而本项目的工具
执行器跑在 AnyIO 线程池里，每次调用可能落在不同线程 → 每个会话配一条**专属 worker 线程**
+ 命令队列，所有浏览器操作都投递到该线程执行。跨线程只传纯数据（str/dict），稳。

安全（三道闸）：
  1. SSRF：所有导航（含点击后的跳转落点）过 net_guard.check_url_safe——挡内网/回环/云元数据/
     DNS rebinding。自建沙箱评测（WebArena 需访问本机自建站）用 HASHMM_BROWSER_ALLOW_PRIVATE=1
     显式放行，默认关。
  2. 只允许 http/https；file:// javascript: data: 一律拒绝。
  3. 资源上限：会话数上限（默认 6）、空闲回收（默认 10 分钟）、页面加载超时（25s）、
     文本截断（快照 3500 字 / read 8000 字）、截图只落到会话工作区。

纯标准库判定 + 可选 playwright；模块可在无任何三方依赖时导入（供单测）。
"""
from __future__ import annotations

import html as _html
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.browser")

# ── 常量（可用环境变量微调，全部有安全默认值）──
_MAX_SESSIONS = int(os.environ.get("HASHMM_BROWSER_MAX_SESSIONS", "6"))
_IDLE_TTL = float(os.environ.get("HASHMM_BROWSER_IDLE_TTL", "600"))       # 秒
_NAV_TIMEOUT_MS = int(os.environ.get("HASHMM_BROWSER_NAV_TIMEOUT_MS", "25000"))
_SNAPSHOT_TEXT_CAP = 3500
_READ_TEXT_CAP = 8000
_MAX_ELEMENTS = 40
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 HashMM-Agent")


def _allow_private() -> bool:
    """是否放行内网目标（WebArena 自建站/本机沙箱评测用；默认关）。"""
    return os.environ.get("HASHMM_BROWSER_ALLOW_PRIVATE", "").strip() == "1"


def check_nav_allowed(url: str) -> tuple[bool, str]:
    """导航目标是否允许。scheme 白名单 + SSRF（可被 ALLOW_PRIVATE 显式放开内网）。"""
    raw = (url or "").strip()
    if not raw:
        return False, "URL 为空"
    # javascript:/data:/vbscript: carry an explicit scheme despite lacking
    # ``://``. Treating every such value as a bare hostname would turn it into
    # ``https://javascript:...`` and bypass the scheme allowlist.
    explicit = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", raw)
    if explicit and explicit.group(1).lower() not in ("http", "https"):
        return False, f"仅允许 http/https（收到 '{explicit.group(1).lower()}'）"
    scheme = urlparse(raw if "://" in raw else "https://" + raw).scheme.lower()
    if scheme not in ("http", "https", ""):
        return False, f"仅允许 http/https（收到 '{scheme}'）"
    if _allow_private():
        return True, "ok(已显式放行内网 HASHMM_BROWSER_ALLOW_PRIVATE=1)"
    try:
        from hashmm.tools.net_guard import check_url_safe
        return check_url_safe(raw)
    except Exception as e:  # noqa: BLE001  防护模块异常 → fail-closed
        return False, f"安全校验不可用：{type(e).__name__}（fail-closed 拒绝）"


# ═══════════════════════ lite 引擎的 HTML 解析（纯 stdlib）═══════════════════════

class _LitePage(HTMLParser):
    """提取可读文本 + 链接表（lite 引擎快照用）。"""

    # 注意：不把 head 放进 _SKIP —— title 是 head 的子元素，跳过 head 会连 title 文本一起吞掉。
    # head 里真正要跳过的 script/style 已单列。
    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []   # (text, href)
        self._cur_href: str | None = None
        self._cur_link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._cur_href = href
            self._cur_link_text = []
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._cur_href is not None:
            txt = re.sub(r"\s+", " ", "".join(self._cur_link_text)).strip()
            if self._cur_href and not self._cur_href.startswith(("javascript:", "#")):
                self.links.append((txt or self._cur_href[:60], self._cur_href))
            self._cur_href = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        if self._cur_href is not None:
            self._cur_link_text.append(data)
        self._text.append(data)

    def text(self) -> str:
        t = _html.unescape("".join(self._text))
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n\s*\n+", "\n", t)
        return t.strip()


# ═══════════════════════ 会话（两种引擎的统一外观）═══════════════════════

@dataclass
class _Snapshot:
    url: str = ""
    title: str = ""
    text: str = ""
    elements: list[dict] = field(default_factory=list)   # {n, kind, text, href?}

    def render(self, cap: int = _SNAPSHOT_TEXT_CAP) -> str:
        body = self.text[:cap] + ("\n…（正文截断）" if len(self.text) > cap else "")
        lines = [f"[页面] {self.title or '(无标题)'}", f"[URL] {self.url}", "", body]
        if self.elements:
            lines += ["", f"[可交互元素]（用 browser_act 的 target 填编号，共 {len(self.elements)} 个）"]
            for el in self.elements:
                extra = f" → {el['href']}" if el.get("href") else ""
                lines.append(f"  [{el['n']}] <{el['kind']}> {el['text'][:80]}{extra}")
        return "\n".join(lines)


class _LiteSession:
    """无 playwright 时的降级引擎：静态抓取 + 链接跳转。"""

    engine = "lite"

    def __init__(self):
        self._history: list[str] = []
        self._snap = _Snapshot()

    def _fetch(self, url: str) -> _Snapshot:
        if _allow_private():
            # 显式放行内网时不能用 safe_get（它必拦内网）→ 走裸 urllib，仍限 http/https。
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as r:   # noqa: S310  scheme 已白名单
                final, raw = r.geturl(), r.read(2_000_000)
        else:
            from hashmm.tools.net_guard import safe_get
            r = safe_get(url, timeout=20, headers={"User-Agent": _UA})
            final, raw = str(r.url), (r.content or b"")[:2_000_000]
        p = _LitePage()
        try:
            p.feed(raw.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001  容忍畸形 HTML
            pass
        els = [{"n": i + 1, "kind": "link", "text": t, "href": urljoin(final, h)}
               for i, (t, h) in enumerate(p.links[:_MAX_ELEMENTS])]
        return _Snapshot(url=final, title=p.title.strip(), text=p.text(), elements=els)

    def open(self, url: str) -> _Snapshot:
        self._snap = self._fetch(url)
        self._history.append(self._snap.url)
        return self._snap

    def act(self, action: str, target: str = "", text: str = "") -> _Snapshot | str:
        if action == "click":
            el = _pick_element(self._snap.elements, target)
            if not el or not el.get("href"):
                return "Error: lite 引擎只能点击带链接的元素（按编号）；该目标不可点。"
            ok, why = check_nav_allowed(el["href"])
            if not ok:
                return f"Error: 目标被安全策略拦截：{why}"
            return self.open(el["href"])
        if action == "back":
            if len(self._history) >= 2:
                self._history.pop()
                return self.open(self._history.pop())
            return "Error: 没有可后退的历史。"
        if action in ("fill", "press", "scroll", "fill_form"):
            return ("Error: 当前为 lite 引擎（未安装 playwright），不支持 fill/press/scroll/fill_form。"
                    "安装：pip install playwright && playwright install chromium")
        if action == "wait":
            return self._snap.render()   # lite 无异步渲染，直接返回当前快照
        if action == "close":
            return "已关闭（lite 会话无持久资源）。"
        return f"Error: 未知动作 {action}"

    def read(self, mode: str) -> str:
        s = self._snap
        if mode == "title":
            return f"{s.title}\n{s.url}"
        if mode == "links":
            return "\n".join(f"[{e['n']}] {e['text']} → {e.get('href','')}" for e in s.elements) or "(无链接)"
        if mode == "tables":
            return ("lite 引擎不做表格结构化提取（需 playwright/Chromium）。"
                    "以下是页面正文，表格内容也在其中：\n\n" + s.text[:_READ_TEXT_CAP])
        return s.text[:_READ_TEXT_CAP] + ("\n…（截断）" if len(s.text) > _READ_TEXT_CAP else "")

    def screenshot(self, path: Path) -> str:
        return "Error: 截图需要 playwright 引擎（当前 lite）。"

    def close(self):
        pass


def _pick_element(elements: list[dict], target: str) -> dict | None:
    t = (target or "").strip()
    if t.isdigit():
        n = int(t)
        for el in elements:
            if el["n"] == n:
                return el
    for el in elements:   # 文本兜底匹配
        if t and t.lower() in (el.get("text") or "").lower():
            return el
    return None


class _PlaywrightSession:
    """Chromium 会话：专属线程 + 命令队列（playwright 同步对象线程亲和）。"""

    engine = "chromium"

    def __init__(self, *, storage_state: str | None = None):
        self._q: "queue.Queue[tuple[Callable, tuple, dict, queue.Queue]]" = queue.Queue()
        self._alive = True
        self._storage_state = storage_state
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="hashmm-browser")
        self._boot_err: str | None = None
        self._booted = threading.Event()
        self._thread.start()
        self._booted.wait(timeout=60)
        if self._boot_err:
            raise RuntimeError(self._boot_err)

    # —— worker 线程内部 ——
    def _worker(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            args = ["--no-sandbox", "--disable-dev-shm-usage"]
            self._browser = self._pw.chromium.launch(headless=True, args=args)
            context_kwargs = {
                "user_agent": _UA,
                "viewport": {"width": 1280, "height": 900},
            }
            if self._storage_state:
                context_kwargs["storage_state"] = self._storage_state
            self._context = self._browser.new_context(**context_kwargs)
            self._page = self._context.new_page()
            self._page.set_default_timeout(_NAV_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            self._boot_err = f"Chromium 启动失败：{type(e).__name__}: {str(e)[:200]}"
            self._alive = False
            self._booted.set()
            return
        self._booted.set()
        while self._alive:
            try:
                fn, a, kw, out = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                out.put(("ok", fn(*a, **kw)))
            except Exception as e:  # noqa: BLE001
                out.put(("err", f"{type(e).__name__}: {str(e)[:300]}"))
        try:
            self._context.close()
            self._browser.close()
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass

    def _call(self, fn: Callable, *a, **kw):
        if not self._alive:
            raise RuntimeError("浏览器会话已关闭")
        out: queue.Queue = queue.Queue()
        self._q.put((fn, a, kw, out))
        status, val = out.get(timeout=max(60.0, _NAV_TIMEOUT_MS / 1000 + 30))
        if status == "err":
            raise RuntimeError(val)
        return val

    # —— 页面操作（全部经 _call 投递到 worker 线程）——
    def _snapshot_impl(self) -> _Snapshot:
        pg = self._page
        title = pg.title() or ""
        url = pg.url or ""
        try:
            text = pg.inner_text("body", timeout=5000)
        except Exception:  # noqa: BLE001
            text = ""
        text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
        els: list[dict] = []
        try:
            raw = pg.evaluate(
                """() => {
                  const sel = 'a[href], button, input, textarea, select, [role="button"]';
                  const out = [];
                  for (const el of document.querySelectorAll(sel)) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) continue;   // 不可见跳过
                    const kind = el.tagName.toLowerCase();
                    let text = (el.innerText || el.value || el.placeholder ||
                                el.getAttribute('aria-label') || '').trim().slice(0, 100);
                    const href = kind === 'a' ? (el.href || '') : '';
                    if (kind === 'a' && href.startsWith('javascript:')) continue;
                    out.push({kind, text, href});
                    if (out.length >= %d) break;
                  }
                  return out;
                }""" % _MAX_ELEMENTS)
            for i, el in enumerate(raw or []):
                els.append({"n": i + 1, "kind": el.get("kind", "?"),
                            "text": el.get("text", ""), "href": el.get("href", "")})
        except Exception:  # noqa: BLE001
            pass
        self._elements = els
        return _Snapshot(url=url, title=title, text=text[:20000], elements=els)

    def open(self, url: str) -> _Snapshot:
        def _go():
            self._page.goto(url, wait_until="domcontentloaded")
            try:
                self._page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:  # noqa: BLE001  SPA 常态，忍
                pass
            return self._snapshot_impl()
        snap = self._call(_go)
        # 落点复检：页面内重定向可能把我们带去内网 → 拦截即关停该页并报告。
        ok, why = check_nav_allowed(snap.url)
        if not ok:
            self._call(lambda: self._page.goto("about:blank"))
            raise PermissionError(f"落点被安全策略拦截：{why}（{snap.url}）")
        return snap

    def act(self, action: str, target: str = "", text: str = "") -> _Snapshot | str:
        els = getattr(self, "_elements", [])
        # ── V310：fill_form 批量填表（一次填多个字段，减少 agent 往返）──
        # target 传 JSON：{"选择器或字段名": "值", ...}，或 text 传同样的 JSON。
        if action == "fill_form":
            import json as _json
            spec_raw = target or text or ""
            try:
                spec = _json.loads(spec_raw) if spec_raw.strip().startswith("{") else {}
            except Exception:  # noqa: BLE001
                spec = {}
            if not spec:
                return "Error: fill_form 需要 JSON，形如 {\"#email\": \"a@b.com\", \"密码\": \"x\"}"

            def _fill_many():
                pg = self._page
                done, failed = [], []
                for sel, val in spec.items():
                    try:
                        # 依次尝试：CSS 选择器 → label 文本 → placeholder → name 属性
                        loc = None
                        for finder in (
                            lambda: pg.locator(sel).first,
                            lambda: pg.get_by_label(sel, exact=False).first,
                            lambda: pg.get_by_placeholder(sel, exact=False).first,
                            lambda: pg.locator(f'[name="{sel}"]').first,
                        ):
                            try:
                                cand = finder()
                                if cand.count() > 0:
                                    loc = cand
                                    break
                            except Exception:  # noqa: BLE001
                                continue
                        if loc is None:
                            failed.append(sel)
                            continue
                        loc.fill(str(val), timeout=6000)
                        done.append(sel)
                    except Exception:  # noqa: BLE001
                        failed.append(sel)
                return self._snapshot_impl(), done, failed

            snap, done, failed = self._call(_fill_many)
            note = f"已填 {len(done)} 个字段" + (f"，{len(failed)} 个没找到：{failed}" if failed else "")
            return note + "\n\n" + snap.render()

        # ── V310：wait 等元素出现（页面加载慢/异步渲染时用，避免过早操作失败）──
        if action == "wait":
            sel = (target or text or "").strip()

            def _wait():
                pg = self._page
                if sel:
                    try:
                        pg.wait_for_selector(sel, timeout=10000)
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    try:
                        pg.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:  # noqa: BLE001
                        pass
                return self._snapshot_impl()
            return self._call(_wait)

        if action in ("click", "fill", "press"):
            el = _pick_element(els, target) if (target or "").strip().isdigit() or action == "click" else None

            def _do():
                pg = self._page
                if el is not None:
                    # 依据快照元素定位：优先 href/文本，兜底第 n 个同类
                    if el.get("href"):
                        loc = pg.locator(f'a[href="{el["href"].replace(chr(34), "")}"]').first
                    elif el.get("text"):
                        loc = pg.get_by_text(el["text"][:60], exact=False).first
                    else:
                        loc = pg.locator("a, button, input, textarea, select").nth(el["n"] - 1)
                else:
                    loc = pg.locator(target).first   # CSS 选择器直连
                if action == "click":
                    loc.click(timeout=8000)
                elif action == "fill":
                    loc.fill(text, timeout=8000)
                elif action == "press":
                    (loc if target else pg.keyboard).press(text or "Enter")
                try:
                    pg.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
                return self._snapshot_impl()
            snap = self._call(_do)
            ok, why = check_nav_allowed(snap.url)
            if not ok:
                self._call(lambda: self._page.goto("about:blank"))
                return f"Error: 落点被安全策略拦截：{why}"
            return snap
        if action == "scroll":
            def _sc():
                delta = -800 if (text or target).strip().lower() in ("up", "上") else 800
                self._page.mouse.wheel(0, delta)
                time.sleep(0.3)
                return self._snapshot_impl()
            return self._call(_sc)
        if action == "back":
            def _bk():
                self._page.go_back(wait_until="domcontentloaded")
                return self._snapshot_impl()
            snap = self._call(_bk)
            ok, why = check_nav_allowed(snap.url)
            return snap if ok else f"Error: 落点被安全策略拦截：{why}"
        if action == "close":
            self.close()
            return "浏览器会话已关闭。"
        return f"Error: 未知动作 {action}"

    def read(self, mode: str) -> str:
        def _rd():
            pg = self._page
            if mode == "title":
                return f"{pg.title()}\n{pg.url}"
            if mode == "links":
                snap = self._snapshot_impl()
                return "\n".join(f"[{e['n']}] {e['text']} → {e.get('href','')}"
                                 for e in snap.elements) or "(无链接)"
            if mode == "html":
                return (pg.content() or "")[:_READ_TEXT_CAP]
            if mode == "tables":
                # V310：把页面里的 <table> 提取成结构化文本（对标大厂浏览器 agent 的数据抓取）
                try:
                    rows = pg.evaluate(
                        """() => {
                          const out = [];
                          for (const tb of document.querySelectorAll('table')) {
                            const trs = [];
                            for (const tr of tb.querySelectorAll('tr')) {
                              const cells = [...tr.querySelectorAll('th,td')]
                                .map(td => (td.innerText || '').trim().replace(/\\s+/g,' '));
                              if (cells.length) trs.push(cells);
                            }
                            if (trs.length) out.push(trs);
                            if (out.length >= 5) break;
                          }
                          return out;
                        }"""
                    )
                    if not rows:
                        return "(页面没有 <table> 元素)"
                    parts = []
                    for ti, tbl in enumerate(rows or []):
                        parts.append(f"[表格 {ti + 1}]")
                        for r in tbl[:50]:
                            parts.append(" | ".join(str(c)[:40] for c in r))
                    return "\n".join(parts)[:_READ_TEXT_CAP]
                except Exception as e:  # noqa: BLE001
                    return f"表格提取失败：{type(e).__name__}"
            t = pg.inner_text("body", timeout=5000) or ""
            return t[:_READ_TEXT_CAP] + ("\n…（截断）" if len(t) > _READ_TEXT_CAP else "")
        return self._call(_rd)

    def screenshot(self, path: Path) -> str:
        def _sh():
            self._page.screenshot(path=str(path), full_page=False)
            return str(path)
        return self._call(_sh)

    def run_on_page(self, callback: Callable):
        """Run an internal evaluator against the exact page the agent operated."""
        return self._call(lambda: callback(self._page))

    def close(self):
        self._alive = False


# ═══════════════════════ 内核：会话池 + 统一入口 ═══════════════════════

class BrowserKernel:
    def __init__(self):
        self._sessions: dict[str, Any] = {}
        self._last_used: dict[str, float] = {}
        self._session_storage: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._pw_available: bool | None = None

    def playwright_available(self) -> bool:
        if self._pw_available is None:
            try:
                import playwright.sync_api  # noqa: F401
                self._pw_available = True
            except Exception:  # noqa: BLE001
                self._pw_available = False
        return self._pw_available

    def _gc(self):
        now = time.time()
        for cid, ts in list(self._last_used.items()):
            if now - ts > _IDLE_TTL:
                self._drop(cid)

    def _drop(self, conv_id: str):
        s = self._sessions.pop(conv_id, None)
        self._last_used.pop(conv_id, None)
        self._session_storage.pop(conv_id, None)
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def _session(self, conv_id: str):
        with self._lock:
            self._gc()
            s = self._sessions.get(conv_id)
            if s is not None:
                self._last_used[conv_id] = time.time()
                return s
            if len(self._sessions) >= _MAX_SESSIONS:
                oldest = min(self._last_used, key=self._last_used.get, default=None)
                if oldest is not None:
                    self._drop(oldest)
            forced = os.environ.get("HASHMM_BROWSER_ENGINE", "").strip().lower()
            use_pw = (forced != "lite") and self.playwright_available()
            if use_pw:
                try:
                    s = _PlaywrightSession(storage_state=self._session_storage.get(conv_id))
                except Exception as e:  # noqa: BLE001  Chromium 起不来（未 install 等）→ 降级
                    logger.warning("Chromium 启动失败，降级 lite 引擎：%s", e)
                    s = _LiteSession()
            else:
                s = _LiteSession()
            self._sessions[conv_id] = s
            self._last_used[conv_id] = time.time()
            logger.info("浏览器会话创建：conv=%s engine=%s（活跃 %d）",
                        conv_id, s.engine, len(self._sessions))
            return s

    # —— 四个工具入口（executor 直接调这里；返回 str 给 LLM）——
    def configure_session(self, conv_id: str, *, storage_state: str | None = None) -> None:
        """Configure auth state before the first browser_open for a conversation."""
        with self._lock:
            if conv_id in self._sessions:
                raise RuntimeError("浏览器会话已创建，不能再更换 storage_state")
            self._session_storage[conv_id] = storage_state

    def open(self, conv_id: str, url: str) -> str:
        ok, why = check_nav_allowed(url)
        if not ok:
            return f"Error: 目标被安全策略拦截：{why}"
        s = self._session(conv_id)
        try:
            snap = s.open(url if "://" in url else "https://" + url)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            return f"Error: 打开失败 {type(e).__name__}: {str(e)[:200]}"
        return f"（引擎：{s.engine}）\n" + snap.render()

    def act(self, conv_id: str, action: str, target: str = "", text: str = "") -> str:
        s = self._sessions.get(conv_id)
        if s is None:
            return "Error: 还没有打开任何页面——先用 browser_open。"
        self._last_used[conv_id] = time.time()
        try:
            r = s.act((action or "").strip().lower(), target or "", text or "")
        except Exception as e:  # noqa: BLE001
            return f"Error: {type(e).__name__}: {str(e)[:200]}"
        if action == "close":
            self._drop(conv_id)
        return r if isinstance(r, str) else r.render()

    def read(self, conv_id: str, mode: str = "text") -> str:
        s = self._sessions.get(conv_id)
        if s is None:
            return "Error: 还没有打开任何页面——先用 browser_open。"
        self._last_used[conv_id] = time.time()
        try:
            return s.read((mode or "text").strip().lower())
        except Exception as e:  # noqa: BLE001
            return f"Error: {type(e).__name__}: {str(e)[:200]}"

    def screenshot(self, conv_id: str, out_dir: Path, name: str = "") -> str:
        s = self._sessions.get(conv_id)
        if s is None:
            return "Error: 还没有打开任何页面——先用 browser_open。"
        self._last_used[conv_id] = time.time()
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", (name or "").strip()) or f"page_{int(time.time())}"
        if not safe.endswith(".png"):
            safe += ".png"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            r = s.screenshot(out_dir / safe)
        except Exception as e:  # noqa: BLE001
            return f"Error: {type(e).__name__}: {str(e)[:200]}"
        return r if r.startswith("Error") else f"截图已保存：{safe}（在会话工作区，可预览/下载）"

    def run_on_page(self, conv_id: str, callback: Callable):
        """Internal benchmark hook; never exposed as an agent tool."""
        s = self._sessions.get(conv_id)
        if s is None:
            raise RuntimeError("还没有打开任何页面")
        if not isinstance(s, _PlaywrightSession):
            raise RuntimeError("官方网页判分需要 Chromium/Playwright 会话")
        self._last_used[conv_id] = time.time()
        return s.run_on_page(callback)

    def close_all(self):
        with self._lock:
            for cid in list(self._sessions):
                self._drop(cid)

    def stats(self) -> dict:
        return {"active": len(self._sessions),
                "engine": "chromium" if self.playwright_available() else "lite"}


_kernel: BrowserKernel | None = None


def get_kernel() -> BrowserKernel:
    global _kernel
    if _kernel is None:
        _kernel = BrowserKernel()
    return _kernel

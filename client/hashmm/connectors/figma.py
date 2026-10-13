"""hashmm/connectors/figma.py — Figma 设计稿导入（Trae design 模式 / 画布融合的一环）。

用途：把一个 Figma 文件转成项目的**画布 HTML**（会话画布/design 承载），从设计直接进入
「就地编辑 / 版本历史 / 划选提问 / 发布」的画布全家桶。

分两层，边界诚实：
  · ``parse_figma_document(figma_json)`` —— **纯解析核心**：把 Figma 文档树（frames/text/
    rect…）按 absoluteBoundingBox 转成绝对定位的 HTML。不碰网络，**可离线单测**。
  · ``fetch_file(file_key, token)`` / ``import_to_html(url_or_key, token)`` —— **实时拉取**：
    调 Figma 官方 REST（GET /v1/files/:key），需要**网络 + 用户的 Figma Personal Access Token**。
    本环境离线跑不了实时拉取，但解析核心已单测；实时路径在你的部署（有网络+token）上生效。

安全：token 只在调用时传入、不落库；解析永不抛错（坏节点跳过），有界（max_nodes 防超大稿卡死）。
"""
from __future__ import annotations

import re
from html import escape as _esc

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.connectors.figma")

FIGMA_API = "https://api.figma.com"
_MAX_NODES = 800


def _n(v) -> str:
    """数字→干净字符串：整数去掉 .0（12.0→12），非整保留一位小数。"""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(round(f, 1))
    except Exception:  # noqa: BLE001
        return "0"


# ── URL / key ─────────────────────────────────────────────────────────────
def file_key_from_url(url_or_key: str) -> str:
    """从 Figma URL 或裸 key 提取文件 key。
    形如 https://www.figma.com/file/ABC123/Name 或 /design/ABC123/Name → ABC123。"""
    s = str(url_or_key or "").strip()
    if not s:
        return ""
    m = re.search(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)", s)
    if m:
        return m.group(1)
    # 裸 key（无斜杠、无协议）
    if "/" not in s and " " not in s:
        return s
    return ""


# ── 纯解析核心（可离线单测）───────────────────────────────────────────────
def _rgba(fills) -> str | None:
    """Figma fills[0].color {r,g,b,a}(0..1)→ css rgba。取第一个可见 SOLID 填充。"""
    try:
        for f in fills or []:
            if f.get("visible", True) is False:
                continue
            if f.get("type") not in (None, "SOLID"):
                continue
            c = f.get("color") or {}
            if not c:
                continue
            r = int(round(float(c.get("r", 0)) * 255))
            g = int(round(float(c.get("g", 0)) * 255))
            b = int(round(float(c.get("b", 0)) * 255))
            a = float(c.get("a", f.get("opacity", 1)))
            return f"rgba({r},{g},{b},{round(a, 3)})"
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return None


def _box(node) -> dict | None:
    b = node.get("absoluteBoundingBox")
    if isinstance(b, dict) and "x" in b and "width" in b:
        return b
    return None


def _walk(node, origin, out, counter, depth=0):
    """深度优先把节点转成绝对定位 div/文本，坐标相对根 origin。"""
    if counter["n"] >= _MAX_NODES or depth > 40:
        return
    try:
        ntype = node.get("type", "")
        box = _box(node)
        if box and ntype not in ("DOCUMENT", "CANVAS", "PAGE"):
            counter["n"] += 1
            left = _n(box["x"] - origin[0])
            top = _n(box["y"] - origin[1])
            w = _n(box.get("width", 0))
            h = _n(box.get("height", 0))
            bg = _rgba(node.get("fills"))
            radius = node.get("cornerRadius")
            if ntype == "TEXT":
                chars = _esc(str(node.get("characters", "")))
                st = node.get("style") or {}
                fs = st.get("fontSize", 14)
                fw = st.get("fontWeight", 400)
                color = _rgba(node.get("fills")) or "#111"
                align = {"LEFT": "left", "CENTER": "center", "RIGHT": "right",
                         "JUSTIFIED": "justify"}.get(st.get("textAlignHorizontal"), "left")
                out.append(
                    f'<div style="position:absolute;left:{left}px;top:{top}px;width:{w}px;'
                    f'font-size:{fs}px;font-weight:{fw};color:{color};text-align:{align};'
                    f'line-height:1.3;white-space:pre-wrap">{chars}</div>')
            else:
                style = (f"position:absolute;left:{left}px;top:{top}px;width:{w}px;height:{h}px;")
                if bg:
                    style += f"background:{bg};"
                if radius:
                    try:
                        style += f"border-radius:{_n(radius)}px;"
                    except Exception:  # noqa: BLE001
                        pass
                # 描边
                strokes = node.get("strokes") or []
                if strokes:
                    sc = _rgba(strokes) or "rgba(0,0,0,.15)"
                    sw = node.get("strokeWeight", 1)
                    style += f"box-sizing:border-box;border:{_n(sw)}px solid {sc};"
                out.append(f'<div style="{style}"></div>')
        for child in node.get("children", []) or []:
            _walk(child, origin, out, counter, depth + 1)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)


def parse_figma_document(figma_json: dict, *, page_index: int = 0,
                         title: str = "Figma 导入") -> str:
    """把 Figma 文件 JSON 转成画布 HTML（绝对定位还原布局）。**永不抛错**。

    取第 page_index 页里的第一个顶层 frame 作为画布，其 bounding box 为坐标原点与画布尺寸。
    找不到可渲染内容 → 返回一段友好的空占位 HTML（而不是报错）。
    """
    try:
        doc = (figma_json or {}).get("document") or {}
        pages = doc.get("children") or []
        if not pages:
            return _empty_html(title, "文件里没有页面")
        page = pages[min(page_index, len(pages) - 1)]
        frames = [c for c in (page.get("children") or []) if _box(c)]
        if not frames:
            return _empty_html(title, "页面里没有可渲染的画框")
        root = frames[0]
        rb = _box(root)
        origin = (rb["x"], rb["y"])
        cw = round(rb.get("width", 800), 1)
        ch = round(rb.get("height", 600), 1)
        page_bg = _rgba(root.get("fills")) or "#ffffff"
        out: list[str] = []
        counter = {"n": 0}
        for child in root.get("children", []) or []:
            _walk(child, origin, out, counter)
        name = _esc(str(figma_json.get("name") or root.get("name") or title))
        body = "\n".join(out) if out else '<div style="padding:24px;color:#888">（空画框）</div>'
        return (
            "<!-- Figma 导入（parse_figma_document）——已进入画布，可就地编辑/发布 -->\n"
            f'<div style="position:relative;width:{cw}px;height:{ch}px;background:{page_bg};'
            f'margin:0 auto;box-shadow:0 2px 24px rgba(0,0,0,.08);overflow:hidden;'
            f'font-family:Inter,system-ui,\'PingFang SC\',sans-serif" data-figma-name="{name}">\n'
            f"{body}\n</div>")
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return _empty_html(title, "解析失败，已生成空画布")


def _empty_html(title: str, note: str) -> str:
    return (f'<div style="padding:40px;text-align:center;color:#888;font-family:system-ui">'
            f'<div style="font-size:18px;font-weight:600;margin-bottom:8px">{_esc(title)}</div>'
            f'<div style="font-size:13px">{_esc(note)}</div></div>')


# ── 实时拉取（需网络 + token）─────────────────────────────────────────────
def fetch_file(file_key: str, token: str, timeout: int = 30) -> dict:
    """调 Figma 官方 REST 取文件 JSON。需要网络 + 用户的 Personal Access Token。

    失败抛 RuntimeError（带人话原因），由调用方转成给用户的可行动提示。
    """
    key = str(file_key or "").strip()
    tok = str(token or "").strip()
    if not key:
        raise RuntimeError("缺少 Figma 文件 key（可粘贴文件链接自动解析）")
    if not tok:
        raise RuntimeError("缺少 Figma Personal Access Token（在 Figma 账号设置里生成，仅本次使用、不会保存）")
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            f"{FIGMA_API}/v1/files/{key}",
            headers={"X-Figma-Token": tok, "User-Agent": "HashMM/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return _json.loads(data.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        # 常见：403 token 无效 / 404 key 不对 / 网络不可达
        msg = str(e)
        if "403" in msg:
            raise RuntimeError("Figma 拒绝访问（403）：Token 无效或无权访问该文件")
        if "404" in msg:
            raise RuntimeError("Figma 文件不存在（404）：检查链接/key 是否正确")
        raise RuntimeError(f"拉取 Figma 文件失败：{msg[:160]}")


def import_to_html(url_or_key: str, token: str, *, page_index: int = 0) -> str:
    """一步到位：URL/key + token → 画布 HTML。实时路径（需网络）。"""
    key = file_key_from_url(url_or_key)
    if not key:
        raise RuntimeError("无法从输入解析出 Figma 文件 key")
    data = fetch_file(key, token)
    return parse_figma_document(data, page_index=page_index,
                                title=str(data.get("name") or "Figma 导入"))

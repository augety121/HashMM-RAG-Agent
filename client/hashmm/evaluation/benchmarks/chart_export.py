"""hashmm/evaluation/benchmarks/chart_export.py —— 对比图导出（SVG + CSV）· V320。

用户的最终诉求：拿一张「HashMM vs 大厂」的对比图。本模块把 vs_frontier 的对比结构
直接渲染成两种可交付格式：

  · render_svg(cmp)  —— 一张自包含的横向条形对比图（纯 SVG 文本，零依赖，
                        浏览器直接打开 / 可插 PPT / 可转 PNG）
  · render_csv(cmp)  —— Excel 友好的 CSV（UTF-8 BOM，中文 Excel 双击即开），
                        每行一条序列（HashMM 或某大厂锚点），带 CI/样本量/可比性

诚实性设计（与 vs_frontier 同一事实来源，绝不另起炉灶）：
  1. 输入就是 build_comparison() 的返回值——可比性门禁只在一处实现，图和
     报告永远一致，不会出现"报告说不可比、图里却并排"的自打脸。
  2. 图分两区：正式对比区（≥50 题，实心条 + 95%CI 误差线）在上；
     趋势区（样本不足）在下，灰色虚边条 + 明确标注"不可与大厂横向比较"。
  3. 缺失的基准不画 0 分条（缺失≠0），只在脚注列出。
  4. HashMM 的条永远带 CI 误差线；大厂锚点是公开数字（无 CI 可画），如实处理。

红线：纯 stdlib（无 matplotlib/plotly），SVG 手工拼接 + XML 转义。
"""
from __future__ import annotations

import time

__all__ = ["render_svg", "render_csv", "chart_meta"]


# ── 视觉常量（自包含配色，脱离 App 主题也好看）──────────────
_C_YOU = "#4f46e5"        # HashMM 条（靛蓝）
_C_YOU_CI = "#1e1b4b"     # CI 误差线（深靛）
_C_ANCHOR = "#94a3b8"     # 大厂锚点条（灰蓝）
_C_TREND = "#cbd5e1"      # 趋势区条（浅灰，虚边）
_C_TEXT = "#0f172a"
_C_SUB = "#64748b"
_C_GRID = "#e2e8f0"
_C_BG = "#ffffff"

_W = 980                   # 总宽
_LABEL_W = 250             # 左侧标签列宽
_VALUE_W = 96              # 右侧数值列宽
_BAR_MAX = _W - _LABEL_W - _VALUE_W - 24
_ROW_H = 24                # 每条序列行高
_GROUP_GAP = 18            # 基准组间距
_HEAD_H = 96               # 图头高（含聚合总结一行）


def _esc(s) -> str:
    """XML 转义（SVG 里出现 & < > 会破坏文档）。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _x(pct: float) -> float:
    """分数(0-100) → 条形像素长。"""
    return max(0.0, min(100.0, float(pct))) / 100.0 * _BAR_MAX


def chart_meta(cmp: dict) -> dict:
    """图的元信息（前端/脚本展示用）：多少基准可比、多少趋势、多少缺失。"""
    return {
        "model": cmp.get("model") or "本机模型",
        "comparable_count": len(cmp.get("comparable") or []),
        "trend_count": len(cmp.get("trend_only") or []),
        "missing_count": len(cmp.get("missing") or []),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
    }


# ════════════════════════════════════════════════════════════════════
# CSV（Excel 友好：UTF-8 BOM + 每行一条序列）
# ════════════════════════════════════════════════════════════════════
def render_csv(cmp: dict) -> str:
    """把对比结构展平成 CSV。列含可比性与 CI，图可从任何工具二次绘制。"""
    def _q(s) -> str:
        s = str(s)
        if any(ch in s for ch in ",\"\n"):
            return '"' + s.replace('"', '""') + '"'
        return s

    rows: list[list] = [[
        "benchmark_id", "benchmark", "series", "score_pct",
        "ci_low", "ci_high", "passed", "n_samples",
        "comparable", "elapsed_min", "verdict_or_note",
    ]]

    def _mins(e):
        ms = int(e.get("elapsed_ms", 0) or 0)
        return round(ms / 60000, 1) if ms > 1000 else ""

    for e in cmp.get("comparable") or []:
        rows.append([e["id"], e["name"], "HashMM(你)", e["your_score"],
                     e["ci_low"], e["ci_high"], e["passed"], e["n"],
                     "yes", _mins(e), e.get("verdict", "")])
        for name, val in e.get("anchors") or []:
            rows.append([e["id"], e["name"], name, val, "", "", "", "",
                         "anchor", "", "大厂公开数字"])
    for e in cmp.get("trend_only") or []:
        rows.append([e["id"], e["name"], "HashMM(你)", e["your_score"],
                     e["ci_low"], e["ci_high"], e["passed"], e["n"],
                     "no", _mins(e), e.get("why_not", "样本不足")])
        for name, val in e.get("anchors") or []:
            rows.append([e["id"], e["name"], name, val, "", "", "", "",
                         "anchor", "", "大厂公开数字（本基准你的样本不足，勿横向比）"])
    for e in cmp.get("missing") or []:
        rows.append([e["id"], e["name"], "HashMM(你)", "", "", "", "", "",
                     "not_run", "", e.get("reason", "尚无跑分记录")])

    body = "\n".join(",".join(_q(c) for c in r) for r in rows)
    return "\ufeff" + body + "\n"        # BOM：中文 Excel 双击即开不乱码


# ════════════════════════════════════════════════════════════════════
# SVG 对比图
# ════════════════════════════════════════════════════════════════════
def _bar_row(y: float, label: str, pct: float, color: str, value_text: str,
             *, ci: tuple[float, float] | None = None, dashed: bool = False) -> list[str]:
    """一行：左标签 + 条 + 右数值；可选 CI 误差线（whisker）。"""
    parts: list[str] = []
    bx = _LABEL_W
    bw = _x(pct)
    bh = _ROW_H - 8
    dash = ' stroke-dasharray="4,3" stroke="#94a3b8" fill-opacity="0.55"' if dashed else ""
    parts.append(f'<text x="{_LABEL_W - 10}" y="{y + _ROW_H / 2 + 4}" text-anchor="end" '
                 f'font-size="12" fill="{_C_TEXT}">{_esc(label)}</text>')
    parts.append(f'<rect x="{bx}" y="{y + 4}" width="{bw:.1f}" height="{bh}" rx="3" '
                 f'fill="{color}"{dash}/>')
    if ci is not None:
        lo, hi = ci
        x1, x2 = bx + _x(lo), bx + _x(hi)
        cy = y + _ROW_H / 2
        parts.append(f'<line x1="{x1:.1f}" y1="{cy}" x2="{x2:.1f}" y2="{cy}" '
                     f'stroke="{_C_YOU_CI}" stroke-width="1.6"/>')
        for xe in (x1, x2):
            parts.append(f'<line x1="{xe:.1f}" y1="{cy - 4}" x2="{xe:.1f}" y2="{cy + 4}" '
                         f'stroke="{_C_YOU_CI}" stroke-width="1.6"/>')
    parts.append(f'<text x="{_LABEL_W + _BAR_MAX + 8}" y="{y + _ROW_H / 2 + 4}" '
                 f'font-size="11.5" fill="{_C_SUB}">{_esc(value_text)}</text>')
    return parts


def _grid(y0: float, y1: float) -> list[str]:
    """0/25/50/75/100 竖向刻度线。"""
    parts = []
    for pct in (0, 25, 50, 75, 100):
        x = _LABEL_W + _x(pct)
        parts.append(f'<line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y1}" '
                     f'stroke="{_C_GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{y1 + 14}" text-anchor="middle" '
                     f'font-size="10" fill="{_C_SUB}">{pct}%</text>')
    return parts


def render_svg(cmp: dict) -> str:
    """渲染整张「HashMM vs 大厂」对比图。返回自包含 SVG 文本。"""
    meta = chart_meta(cmp)
    comparable = cmp.get("comparable") or []
    trend_only = cmp.get("trend_only") or []
    missing = cmp.get("missing") or []

    # ── 预算高度 ──
    def _group_h(e) -> float:
        return _ROW_H * (1 + len(e.get("anchors") or [])) + _GROUP_GAP + 18  # +组名行

    h = _HEAD_H
    h += 26                                   # 正式区标题
    if comparable:
        h += sum(_group_h(e) for e in comparable)
    else:
        h += 34                               # 空态提示
    h += 30                                   # 趋势区标题
    if trend_only:
        h += sum(_group_h(e) for e in trend_only)
    else:
        h += 24
    h += 24                                   # 刻度文字
    h += (18 + 14 * len(missing)) if missing else 0
    h += 44                                   # 脚注
    H = int(h)

    S: list[str] = []
    S.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{H}" '
             f'viewBox="0 0 {_W} {H}" font-family="system-ui,-apple-system,\'PingFang SC\','
             f'\'Microsoft YaHei\',sans-serif">')
    S.append(f'<rect width="{_W}" height="{H}" fill="{_C_BG}"/>')

    # ── 图头 ──
    S.append(f'<text x="24" y="34" font-size="19" font-weight="700" fill="{_C_TEXT}">'
             f'HashMM（{_esc(meta["model"])}）vs 2026 大厂 · 外部基准对比</text>')
    S.append(f'<text x="24" y="56" font-size="12" fill="{_C_SUB}">'
             f'生成 {meta["generated_at"]} · 可比 {meta["comparable_count"]} 项 / '
             f'趋势 {meta["trend_count"]} 项 / 未跑 {meta["missing_count"]} 项 · '
             f'误差线为 Wilson 95% 置信区间</text>')
    # 聚合总结（一句话"你和大厂比得怎么样"）——只统计正式对比区，诚实不充数
    from .vs_frontier import comparison_summary
    _summary = comparison_summary(cmp)
    S.append(f'<text x="24" y="76" font-size="12.5" font-weight="600" fill="{_C_YOU}">'
             f'📊 {_esc(_summary["headline"])}</text>')
    # 图例
    lx = _W - 330
    S.append(f'<rect x="{lx}" y="24" width="14" height="10" rx="2" fill="{_C_YOU}"/>')
    S.append(f'<text x="{lx + 20}" y="33" font-size="11" fill="{_C_SUB}">HashMM(你)</text>')
    S.append(f'<rect x="{lx + 105}" y="24" width="14" height="10" rx="2" fill="{_C_ANCHOR}"/>')
    S.append(f'<text x="{lx + 125}" y="33" font-size="11" fill="{_C_SUB}">大厂公开数字</text>')
    S.append(f'<line x1="{lx + 232}" y1="29" x2="{lx + 258}" y2="29" stroke="{_C_YOU_CI}" stroke-width="1.6"/>')
    S.append(f'<text x="{lx + 264}" y="33" font-size="11" fill="{_C_SUB}">95%CI</text>')

    y = float(_HEAD_H)
    grid_top = y

    # ── 正式对比区 ──
    S.append(f'<text x="24" y="{y + 14}" font-size="13.5" font-weight="700" '
             f'fill="{_C_TEXT}">✅ 正式对比区（样本量达标，可与大厂横向比较）</text>')
    y += 26
    if not comparable:
        S.append(f'<text x="24" y="{y + 14}" font-size="12" fill="{_C_SUB}">'
                 f'暂无达标基准——用 HASHMM_BENCH_SAMPLE=standard(50题) 或 full 重跑即可进入本区。</text>')
        y += 34
    for e in comparable:
        S.append(f'<text x="{_LABEL_W - 10}" y="{y + 13}" text-anchor="end" font-size="12.5" '
                 f'font-weight="700" fill="{_C_TEXT}">{_esc(e["name"])}</text>')
        y += 18
        you_txt = f'{e["your_score"]}%（{e["passed"]}/{e["n"]}）'
        S += _bar_row(y, "HashMM(你)", e["your_score"], _C_YOU, you_txt,
                      ci=(e["ci_low"], e["ci_high"]))
        y += _ROW_H
        for name, val in e.get("anchors") or []:
            S += _bar_row(y, name, val, _C_ANCHOR, f"{val}%")
            y += _ROW_H
        y += _GROUP_GAP

    # ── 趋势区 ──
    S.append(f'<text x="24" y="{y + 14}" font-size="13.5" font-weight="700" '
             f'fill="{_C_TEXT}">⚠️ 趋势区（样本不足 · 仅供看自己迭代，不可与大厂横向比较）</text>')
    y += 30
    if not trend_only:
        S.append(f'<text x="24" y="{y + 6}" font-size="12" fill="{_C_SUB}">无。</text>')
        y += 24
    for e in trend_only:
        S.append(f'<text x="{_LABEL_W - 10}" y="{y + 13}" text-anchor="end" font-size="12.5" '
                 f'font-weight="700" fill="{_C_SUB}">{_esc(e["name"])}</text>')
        y += 18
        you_txt = f'{e["your_score"]}%（{e["passed"]}/{e["n"]}题·CI宽{e["ci_width"]}分）'
        S += _bar_row(y, "HashMM(你)·样本不足", e["your_score"], _C_TREND, you_txt,
                      ci=(e["ci_low"], e["ci_high"]), dashed=True)
        y += _ROW_H
        for name, val in e.get("anchors") or []:
            S += _bar_row(y, name, val, _C_ANCHOR, f"{val}%")
            y += _ROW_H
        y += _GROUP_GAP

    # 网格线画在两区身后（先算完 y 再插到最前面会盖住文字——这里直接补画在底层顺序前面即可）
    S[3:3] = _grid(grid_top, y)               # 插在背景矩形之后、内容之前
    y += 24                                    # 给刻度文字留高

    # ── 缺失区（只列文字，不画 0 分条）──
    if missing:
        S.append(f'<text x="24" y="{y + 12}" font-size="12" font-weight="700" '
                 f'fill="{_C_SUB}">⬜ 尚未跑分（缺失≠0，不入图）：</text>')
        y += 18
        for e in missing:
            top = (e.get("anchors") or [("-", 0)])[0]
            S.append(f'<text x="36" y="{y + 11}" font-size="11" fill="{_C_SUB}">'
                     f'{_esc(e["name"])}（大厂锚点 {_esc(top[0])} {top[1]}%）</text>')
            y += 14

    # ── 脚注 ──
    S.append(f'<text x="24" y="{y + 26}" font-size="10.5" fill="{_C_SUB}">'
             f'诚实声明：正式区分数的判分口径与官方一致（见口径对齐表）；趋势区样本不足，横向比较无统计意义；'
             f'缺失的分数不是 0，是还没跑。</text>')

    S.append("</svg>")
    return "".join(S)

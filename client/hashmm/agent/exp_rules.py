"""hashmm/agent/exp_rules.py — 经验回灌二期（V75）。

一期（loop_insights）把遥测变成人看的报告；二期把高置信的失败模式
**自动沉淀为负面规则**注入系统提示——"让系统越用越聪明"从看报告变成自动生效。

安全设计（铁律）：
- 默认关闭：HASHMM_EXP_RULES=1 才启用；
- 永不抛错：任何故障返回空串，不影响主链路；
- 高阈值：失败率 ≥30% 且失败 ≥3 次才生成规则（防小样本噪音）；
- 限量：最多 3 条规则、总长 ≤600 字（防提示膨胀）；
- 带 TTL 缓存：10 分钟一次盘扫，不拖慢每个请求。
"""
from __future__ import annotations

import os
import time

_CACHE: dict = {"ts": 0.0, "text": ""}
_TTL_S = 600


def generate_rules(report: dict) -> list[str]:
    """从 loop_insights.analyze() 报告生成负面规则。纯函数可测。

    规则与阈值：
    - 工具失败热点：rate>=0.30 且 fails>=3 → 提醒谨慎使用该工具；
    - deadline 停止占比 >=30%（且样本>=5）→ 提醒拆小步骤尽早交付。
    """
    rules: list[str] = []
    if not report or not report.get("runs"):
        return rules
    for h in report.get("tool_fail_hotspots", []) or []:
        try:
            if h.get("rate", 0) >= 0.30 and h.get("fails", 0) >= 3:
                rules.append(
                    f"工具 {h['tool']} 近期失败率 {h['rate']*100:.0f}%"
                    f"（{h['fails']}/{h['calls']}）——调用前仔细核对参数，"
                    "失败一次后立即换方法，不要原样重试。")
        except Exception:
            continue
    try:
        runs = report.get("runs", 0)
        ddl = (report.get("stop_reasons") or {}).get("deadline", 0)
        if runs >= 5 and ddl / runs >= 0.30:
            rules.append(
                f"近期 {ddl}/{runs} 次任务因超时被截停——优先把任务拆成小步骤，"
                "尽早交付部分成果，不要把所有工作堆到最后一轮。")
    except Exception:
        pass
    return rules[:3]


def load_exp_rules(days: int = 7) -> str:
    """读遥测 → 生成规则文本（带 TTL 缓存；默认关闭；永不抛错）。

    返回可直接拼进系统提示的文本段；未开启/无数据/出错均返回 ""。
    """
    if os.environ.get("HASHMM_EXP_RULES") != "1":
        return ""
    now = time.time()
    if now - _CACHE["ts"] < _TTL_S:
        return _CACHE["text"]
    text = ""
    try:
        from hashmm.tools.loop_insights import load_runs, analyze
        rules = generate_rules(analyze(load_runs(days=days)))
        if rules:
            text = ("## 运行经验（遥测自动沉淀，供参考）\n- "
                    + "\n- ".join(rules))[:600]
    except Exception:
        text = ""
    _CACHE["ts"] = now
    _CACHE["text"] = text
    return text


def _reset_cache_for_tests() -> None:
    _CACHE["ts"] = 0.0
    _CACHE["text"] = ""

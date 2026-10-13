"""事件驱动自动化（V257）——"从「你问它才动」变成「事情一发生它就动」"。

订阅全局工作区（GWT 黑板）的广播流：任何模块的重要事件（团队完成/失败、深检出结果、
文档工坊产出、派活失败…）命中规则即**自动行动**——目前的动作是推系统通知
（通知中心 + App 同源可见），不用用户盯着、不用轮询。

设计要点：
  · 规则表是产品资产：加一条规则=加一行（module/kind 前缀匹配 → 通知模板）。
  · settings『event_rules_off』存被用户关闭的规则 id（JSON 数组）——默认全开。
  · 观察层纪律：整个引擎全链路吞异常，永不拖垮工作区广播与执行主链。
  · GET/POST /api/gw/rules 由 workspace_gw 路由暴露，总控中枢「事件自动化」卡管理。
"""
from __future__ import annotations

import json

from hashmm.utils import get_logger

logger = get_logger("hashmm.event_rules")

# (id, 模块, kind 前缀, 人话名称, 通知模板——{summary} 为事件摘要)
RULES: list[dict] = [
    {"id": "team_done",   "module": "team",       "kind": "done",   "name": "多智能体完成即通知",
     "tmpl": "团队任务完成：{summary}"},
    {"id": "team_fail",   "module": "team",       "kind": "fail",   "name": "多智能体失败即报警",
     "tmpl": "团队任务失败：{summary}"},
    {"id": "deep_done",   "module": "deepsearch", "kind": "done",   "name": "深度检索出结果即通知",
     "tmpl": "深度检索完成：{summary}"},
    {"id": "doc_done",    "module": "docstudio",  "kind": "",       "name": "文档工坊产出即通知",
     "tmpl": "文档工坊：{summary}"},
    {"id": "dispatch_fail", "module": "dispatch", "kind": "fail",   "name": "电脑派活失败即报警",
     "tmpl": "派活失败：{summary}"},
    {"id": "loop_done",   "module": "loops",      "kind": "done",   "name": "循环达成即通知",
     "tmpl": "{summary}"},
    {"id": "loop_fail",   "module": "loops",      "kind": "fail",   "name": "循环未达成即报警",
     "tmpl": "{summary}"},
    {"id": "loop_abnormal", "module": "loops",    "kind": "tick_abnormal", "name": "巡检发现异常即报警",
     "tmpl": "{summary}"},
]
_RULE_BY_ID = {r["id"]: r for r in RULES}
_registered = False


def _off_ids() -> set[str]:
    try:
        from hashmm.api.settings_store import get_setting
        v = json.loads(get_setting("event_rules_off", "[]") or "[]")
        return {str(x) for x in v} if isinstance(v, list) else set()
    except Exception:
        return set()


def set_rule(rule_id: str, on: bool) -> bool:
    if rule_id not in _RULE_BY_ID:
        return False
    try:
        from hashmm.api.settings_store import get_setting, set_setting
        v = json.loads(get_setting("event_rules_off", "[]") or "[]")
        off = {str(x) for x in v} if isinstance(v, list) else set()
        (off.discard if on else off.add)(rule_id)
        set_setting("event_rules_off", json.dumps(sorted(off)))
        return True
    except Exception:
        return False


def list_rules() -> list[dict]:
    off = _off_ids()
    return [{"id": r["id"], "name": r["name"],
             "module": r["module"], "on": r["id"] not in off} for r in RULES]


def _on_event(ev: dict) -> None:
    """gw 广播回调：规则匹配 → 推通知。全链路吞异常。"""
    try:
        module = str(ev.get("module") or "")
        kind = str(ev.get("kind") or "")
        summary = str(ev.get("summary") or "")[:140]
        off = _off_ids()
        for r in RULES:
            if r["id"] in off or r["module"] != module:
                continue
            if r["kind"] and not kind.startswith(r["kind"]):
                continue
            try:
                from hashmm.api.routes.notifications import push_notification
                push_notification(
                    key=f"evrule-{r['id']}-{int(float(ev.get('ts') or 0))}",
                    ntype="event", text=r["tmpl"].format(summary=summary),
                    by=str(ev.get("user") or ""))
            except Exception as ne:  # noqa: BLE001
                logger.debug("[event_rules] 通知失败：%s", ne)
            break   # 一条事件最多命中一条规则（按表序优先）
    except Exception as e:  # noqa: BLE001
        logger.debug("[event_rules] 回调异常：%s", e)


def register() -> None:
    """挂到全局工作区广播（幂等）。在 server 启动阶段调用。"""
    global _registered
    if _registered:
        return
    try:
        from hashmm.agent.global_workspace import gw
        gw().subscribe(_on_event)
        _registered = True
        logger.info("[event_rules] 事件驱动已注册（%d 条规则）", len(RULES))
    except Exception as e:  # noqa: BLE001
        logger.warning("[event_rules] 注册失败：%s", e)

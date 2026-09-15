"""skill_curator — 技能后台策展（V97，对标 Hermes curator）。

Hermes 的 curator 在 agent 闲时复查自创建技能：按活跃度自动归档久未用的、
合并重复的——**严格只归档不删除（可恢复）、置顶技能豁免、用辅助通道不碰主
会话缓存**。HashMM 已有 skills 表（quality_score / use_count / last_used），
这里实现同范式的轻量策展：

- `assess_skills(skills, now, archive_after_days)` 纯函数——给出每个技能的
  建议动作（keep / archive / low_quality_flag），不直接落库；
- `run_curation(...)` 把建议落到 skills 表的 metadata（标记 archived，
  不 DELETE）；置顶（pinned，约定写在 trigger_patterns 的元信息或单列）豁免；
- 默认关：HASHMM_SKILL_CURATOR=1 才在闲时触发（与"新模块默认关"铁律一致）。

纯逻辑可单测，不依赖 fastapi。
"""
from __future__ import annotations

import time

DEFAULT_ARCHIVE_AFTER_DAYS = 30
LOW_QUALITY_THRESHOLD = 0.3
MIN_USE_FOR_KEEP = 1


def assess_skill(skill: dict, now: float, archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS) -> dict:
    """评估单个技能，返回 {id, action, reason}。纯函数，不落库。

    action ∈ {keep, archive, flag_low_quality}
    规则（对齐 Hermes：只归档不删、置顶豁免）：
      - pinned（置顶）→ 永远 keep
      - 久未使用（last_used 超过 archive_after_days）且 use_count 低 → archive
      - quality_score 低于阈值但仍在用 → flag_low_quality（提示复查，不归档）
      - 否则 keep
    """
    if skill.get("pinned"):
        return {"id": skill.get("id"), "action": "keep", "reason": "置顶豁免"}

    last_used = skill.get("last_used") or skill.get("created_at") or 0
    use_count = skill.get("use_count") or 0
    quality = skill.get("quality_score", 0.5)
    idle_days = (now - last_used) / 86400 if last_used else 9999

    if idle_days > archive_after_days and use_count < MIN_USE_FOR_KEEP + 1:
        return {"id": skill.get("id"), "action": "archive",
                "reason": f"闲置 {int(idle_days)} 天且使用 {use_count} 次"}
    if quality < LOW_QUALITY_THRESHOLD and use_count >= MIN_USE_FOR_KEEP:
        return {"id": skill.get("id"), "action": "flag_low_quality",
                "reason": f"质量分 {quality:.2f} 偏低，建议复查"}
    return {"id": skill.get("id"), "action": "keep", "reason": "活跃"}


def assess_skills(skills: list, now: float | None = None,
                  archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS) -> list:
    now = now if now is not None else time.time()
    return [assess_skill(s, now, archive_after_days) for s in skills]


def summarize(assessments: list) -> dict:
    """汇总建议动作分布。"""
    out = {"keep": 0, "archive": 0, "flag_low_quality": 0}
    for a in assessments:
        out[a["action"]] = out.get(a["action"], 0) + 1
    return out


def should_run(last_run_at: float, interval_hours: int = 24, now: float | None = None) -> bool:
    """闲时触发判据：距上次策展超过 interval_hours 才跑。"""
    now = now if now is not None else time.time()
    return (now - (last_run_at or 0)) >= interval_hours * 3600


def run_curation(get_skills, archive_skill, *, enabled: bool,
                 archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
                 now: float | None = None) -> dict:
    """执行策展（依赖注入便于测试）。
    @param get_skills    () -> list[dict]
    @param archive_skill (skill_id, reason) -> None   （只标记归档，不删除）
    @param enabled       HASHMM_SKILL_CURATOR 门控
    返回 {ran, summary, archived:[...]}。
    """
    if not enabled:
        return {"ran": False, "reason": "未启用（HASHMM_SKILL_CURATOR=1 开启）"}
    skills = get_skills() or []
    assessments = assess_skills(skills, now=now, archive_after_days=archive_after_days)
    archived = []
    for a in assessments:
        if a["action"] == "archive":
            try:
                archive_skill(a["id"], a["reason"])
                archived.append(a["id"])
            except Exception:
                pass
    return {"ran": True, "summary": summarize(assessments), "archived": archived}

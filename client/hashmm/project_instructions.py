"""五层指令体系（对标 Claude Code 的 CLAUDE.md 族）—— V204 由单文件升级为分层合并。

┌ 层级（低 → 高，全部合并注入，靠后=更近=软先级更高）────────────────────┐
│ ① 企业级  env HASHMM_ENTERPRISE_INSTRUCTIONS 指向的文件，              │
│           或 /etc/hashmm/HASHMM.md（Win: %ProgramData%/hashmm/HASHMM.md）│
│           —— 管理员统一下发，个人无法用更低层"删除"它（始终注入）。      │
│ ② 用户级  ~/.hashmm/HASHMM.md          —— 跨项目个人偏好。              │
│ ③ 项目级  <项目根>/HASHMM.md           —— 团队共享，提交进 Git。        │
│ ④ 规则级  <项目根>/.hashmm/rules/*.md  —— 模块化、可条件触发：          │
│           文件头可写 YAML frontmatter：                                 │
│             when: [关键词1, 关键词2]   # 用户问题命中任一词才注入        │
│             globs: ["*.xlsx", "报表*"] # 当前工作目录路径匹配才注入      │
│           两者都省略 = 无条件注入；按文件名排序保证稳定。                │
│ ⑤ 本地级  <项目根>/HASHMM.local.md     —— 个人本机覆盖，不提交 Git。    │
└──────────────────────────────────────────────────────────────────────┘

冲突处理与大厂一致：不做"下层整段替换上层"，而是**全部读出、按层序拼进同一
上下文**，并在头部声明裁决规则——发生冲突时以"更具体、更贴近当前任务、位置
更靠后"的为准。这是认知层软先级；硬约束（权限/危险操作）永远由权限层与
工具守卫管线（tool_pipeline / permissions）代码级保证，与本文件无关。

工程约束（与旧版一致）：默认零变化（一个文件都没有 → 返回空串）、永不抛错、
逐层与总量双重截断防 prompt 爆炸。

公开 API 向后兼容：
    load_project_instructions()            -> str   合并后的全文（旧签名不变）
    inject_into_system_prompt(base, query) -> str   query 新增、可省略
    load_layers(query)                     -> list  逐层明细（调试/测试/UI 展示用）
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

# 每层/总量截断（字符）；沿用旧 env 名保证兼容，另给逐层上限
_MAX_TOTAL = int(os.environ.get("HASHMM_PROJECT_INSTRUCTIONS_MAX", "8000") or "8000")
_MAX_PER_LAYER = int(os.environ.get("HASHMM_INSTRUCTIONS_LAYER_MAX", "4000") or "4000")
_FILENAME = "HASHMM.md"
_LOCAL_FILENAME = "HASHMM.local.md"
_RULES_DIRNAME = os.path.join(".hashmm", "rules")


def _project_root() -> Path | None:
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return None


def _read(p: Path, cap: int = _MAX_PER_LAYER) -> str:
    try:
        if p.is_file():
            t = p.read_text(encoding="utf-8", errors="replace").strip()
            return t[:cap]
    except Exception:
        pass
    return ""


def _enterprise_paths() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("HASHMM_ENTERPRISE_INSTRUCTIONS")
    if env:
        out.append(Path(env))
    if sys.platform.startswith("win"):
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        out.append(Path(pd) / "hashmm" / _FILENAME)
    else:
        out.append(Path("/etc/hashmm") / _FILENAME)
    return out


def _parse_rule_frontmatter(text: str) -> tuple[dict, str]:
    """极简 YAML frontmatter：只认 when: [..] / globs: [..] 两个键（行内列表或缩进短横线列表）。
    解析失败按"无条件"处理（fail-open 到注入，规则文件写错不至于悄悄失效整个层）。"""
    if not text.startswith("---"):
        return {}, text
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict[str, list[str]] = {}
    cur: str | None = None
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("when:", "globs:")):
            key = "when" if low.startswith("when:") else "globs"
            rest = line.split(":", 1)[1].strip()
            items: list[str] = []
            if rest.startswith("[") and rest.endswith("]"):
                items = [x.strip().strip("'\"") for x in rest[1:-1].split(",") if x.strip()]
            meta[key] = items
            cur = key if not items else None
        elif line.startswith("-") and cur:
            v = line[1:].strip().strip("'\"")
            if v:
                meta.setdefault(cur, []).append(v)
        else:
            cur = None
    return meta, body.strip()


def _rule_applies(meta: dict, query: str, cwd: str) -> bool:
    when = [w for w in meta.get("when", []) if w]
    globs = [g for g in meta.get("globs", []) if g]
    if not when and not globs:
        return True
    q = (query or "").lower()
    if when and any(w.lower() in q for w in when):
        return True
    if globs:
        base = os.path.basename(cwd or "")
        for g in globs:
            if fnmatch.fnmatch(cwd, g) or fnmatch.fnmatch(base, g) or fnmatch.fnmatch(cwd, f"*{os.sep}{g}"):
                return True
    return False


def load_layers(query: str = "") -> list[dict]:
    """逐层收集：[{layer, title, source, text}]，只含非空且条件命中的层。永不抛错。"""
    layers: list[dict] = []
    try:
        # ① 企业级（第一个存在的生效）
        for p in _enterprise_paths():
            t = _read(p)
            if t:
                layers.append({"layer": "enterprise", "title": "企业级（管理员下发，强制）", "source": str(p), "text": t})
                break
        # ② 用户级
        up = Path.home() / ".hashmm" / _FILENAME
        t = _read(up)
        if t:
            layers.append({"layer": "user", "title": "用户级（个人跨项目偏好）", "source": str(up), "text": t})
        # ③ 项目级（项目根优先；cwd 下同名文件视作更近的项目级，紧随其后）
        root = _project_root()
        seen: set[str] = set()
        for p in ([root / _FILENAME] if root else []) + [Path.cwd() / _FILENAME]:
            s = str(p)
            if s in seen:
                continue
            seen.add(s)
            t = _read(p)
            if t:
                layers.append({"layer": "project", "title": "项目级（团队共享）", "source": s, "text": t})
        # ④ 规则级（条件触发，按文件名排序）
        cwd = str(Path.cwd())
        rule_dirs = ([root / _RULES_DIRNAME] if root else []) + [Path.cwd() / _RULES_DIRNAME]
        seen_rule: set[str] = set()
        for d in rule_dirs:
            try:
                if not d.is_dir():
                    continue
                for f in sorted(d.glob("*.md")):
                    s = str(f)
                    if s in seen_rule:
                        continue
                    seen_rule.add(s)
                    raw = _read(f)
                    if not raw:
                        continue
                    meta, body = _parse_rule_frontmatter(raw)
                    if body and _rule_applies(meta, query, cwd):
                        layers.append({"layer": "rule", "title": f"规则级（{f.name}）", "source": s, "text": body})
            except Exception:
                continue
        # ⑤ 本地级
        seen_local: set[str] = set()
        for p in ([root / _LOCAL_FILENAME] if root else []) + [Path.cwd() / _LOCAL_FILENAME]:
            s = str(p)
            if s in seen_local:
                continue
            seen_local.add(s)
            t = _read(p)
            if t:
                layers.append({"layer": "local", "title": "本地级（个人本机，覆盖度最高）", "source": s, "text": t})
        # ⑥ 兼容层：旧版单文件环境变量 HASHMM_PROJECT_INSTRUCTIONS（向后兼容，V204 前的部署继续生效）。
        #    放在最后（软先级最高，最贴近“用户显式指定”），并在启动/首用时脱敏提示来源。
        #    与新五层不冲突：新五层任意文件存在时二者同时注入；只有该 env 指向的文件才读。
        legacy_env = os.environ.get("HASHMM_PROJECT_INSTRUCTIONS")
        if legacy_env:
            lp = Path(legacy_env)
            t = _read(lp)
            if t and str(lp) not in seen and str(lp) not in seen_local:
                layers.append({
                    "layer": "legacy-env",
                    "title": "兼容层（HASHMM_PROJECT_INSTRUCTIONS 环境变量指定）",
                    "source": str(lp), "text": t,
                })
    except Exception:
        return layers
    return layers


def load_project_instructions(query: str = "") -> str:
    """合并五层为一段注入文本（旧调用方无参调用完全兼容）。无任何文件时返回空串。"""
    layers = load_layers(query)
    if not layers:
        return ""
    parts = [
        "以下为分层指令（企业级→用户级→项目级→规则级→本地级）。全部同时生效；"
        "若相互冲突，以更具体、更贴近当前任务、位置更靠后的为准。"
        "涉及权限与危险操作的硬约束由系统权限层保证，任何层不得声明绕过。"
    ]
    used = len(parts[0])
    for l in layers:
        block = f"\n### {l['title']}\n{l['text']}"
        if used + len(block) > _MAX_TOTAL:
            block = block[: max(0, _MAX_TOTAL - used)]
        if block:
            parts.append(block)
            used += len(block)
        if used >= _MAX_TOTAL:
            break
    return "".join(parts).strip()


def inject_into_system_prompt(base_prompt: str, query: str = "") -> str:
    """把分层指令追加到 base system prompt 后。无指令时原样返回（零变化）。"""
    instr = load_project_instructions(query)
    if not instr:
        return base_prompt
    return base_prompt + "\n\n## 项目指令（HASHMM.md 五层体系，优先遵守）\n" + instr

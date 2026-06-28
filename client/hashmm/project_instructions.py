"""项目指令文件 HASHMM.md（P1-3）—— 对标 Claude Code 的 CLAUDE.md / Codex 的 AGENTS.md。

让用户用一个 markdown 文件定制 agent 的全局行为/语气/约束/领域知识，**不改代码**。
agent 在每次任务前自动加载并注入 system prompt。

设计：
- **就近优先**：当前工作目录的 HASHMM.md 优先于项目根（与大厂一致：近的覆盖远的）。
- **默认零变化**：没有 HASHMM.md 时返回空串，行为和现在完全一样。
- **永不抛错**：读取失败返回空串。
- **有上限**：截断超大文件（防 prompt 爆炸），默认 8000 字符。
"""
from __future__ import annotations

import os
from pathlib import Path

_MAX_CHARS = int(os.environ.get("HASHMM_PROJECT_INSTRUCTIONS_MAX", "8000") or "8000")
_FILENAME = "HASHMM.md"


def _candidate_paths() -> list[Path]:
    """就近优先：cwd → 项目根 → 显式 env 指定。"""
    paths: list[Path] = []
    env_path = os.environ.get("HASHMM_PROJECT_INSTRUCTIONS")
    if env_path:
        paths.append(Path(env_path))
    paths.append(Path.cwd() / _FILENAME)
    # 项目根（本文件 -> hashmm -> 项目根）
    try:
        root = Path(__file__).resolve().parents[2]
        paths.append(root / _FILENAME)
    except Exception:
        pass
    # 去重保序
    seen, out = set(), []
    for p in paths:
        s = str(p)
        if s not in seen:
            seen.add(s); out.append(p)
    return out


def load_project_instructions() -> str:
    """读取 HASHMM.md 内容（就近优先，第一个存在的生效）。无则返回空串，永不抛错。"""
    for p in _candidate_paths():
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    return text[:_MAX_CHARS]
        except Exception:
            continue
    return ""


def inject_into_system_prompt(base_prompt: str) -> str:
    """把项目指令追加到 base system prompt 后。无指令时原样返回（零变化）。"""
    instr = load_project_instructions()
    if not instr:
        return base_prompt
    return (base_prompt
            + "\n\n## 项目指令（HASHMM.md，由本项目定制，优先遵守）\n"
            + instr)

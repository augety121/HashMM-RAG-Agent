"""hashmm/memory/layered.py — 分层长期记忆（V294，移植自 TencentDB Agent Memory 的语义金字塔）。

移植来源与理念（README 原文可查，非照搬 TypeScript 实现，而是把方法论用 Python 重写）：
  TencentDB Agent Memory 拒绝"把对话切碎丢进一个扁平向量堆"，改用**分层记忆**：
    L0 Conversation（原始对话） → L1 Atom（原子事实） → L2 Scenario（情境块） → L3 Persona（用户画像）
  下层保留证据、上层保留结构；日常只注入顶层 Persona，需要细节时才逐层下钻（drill-down）。
  实测接入后 PersonaMem 准确率从 48% → 76%。

适配到 HashMM（做减法，全部踩在文件存储上，零新表零新依赖，与 user_memory.py 同一磁盘纪律）：
  · L0：直接复用会话消息（database.create_message），本模块不重复存原始对话。
  · L1：原子事实以 JSONL 落 data/layered_memory/<uid>/l1.jsonl，每条含
        {id, content, type(persona|episodic|instruction), priority, scene_name,
         source_message_ids, metadata, created, updated}。
  · L2：情境块以 META 分隔的 Markdown 落 data/layered_memory/<uid>/scenes/<scene>.md
        （created/updated/summary/heat + 正文），对应 TencentDB 的 Scene Block 文件格式。
  · L3：用户画像落 data/layered_memory/<uid>/persona.md（人类可读的白盒画像）。

铁律（与项目一致）：
  · **默认关**：HASHMM_LAYERED_MEMORY=1 才写盘/注入；关闭时 extract/recall 均安全空返回。
  · **永不抛错**：任何 IO / LLM / 解析故障静默降级，绝不拖垮主链路。
  · **宁缺毋滥**：priority 低于阈值的原子事实丢弃；抽取失败返回空而非编造。
  · **updating-not-creating**：同主体同类事实用去重决策更新而非无限堆积。
  · **容量护栏**：每用户 L1 ≤ 400 条、情境 ≤ 60 个、单条 content ≤ 300 字、注入 ≤ 1200 字。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
import uuid
from functools import wraps
from dataclasses import dataclass, field, asdict
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.memory.layered")
_EXTRACT_LOCK = threading.RLock()


def _serialized_extract(fn):
    """同一进程内串行化“读已有原子→去重→写回”，避免并发抽取丢更新。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _EXTRACT_LOCK:
            return fn(*args, **kwargs)
    return wrapper

# ── 容量护栏 ────────────────────────────────────────────────────────────
MAX_ATOMS = 400
MAX_SCENES = 60
MAX_CONTENT_LEN = 300
MAX_INJECT_LEN = 1200
MAX_L0_FOR_EXTRACT = 40          # 每次抽取最多看的近端消息条数

ATOM_TYPES = ("persona", "episodic", "instruction")

_META_START = "-----META-START-----"
_META_END = "-----META-END-----"


def enabled() -> bool:
    return os.environ.get("HASHMM_LAYERED_MEMORY", "0").strip().lower() in {"1", "true", "yes", "on"}


def _root(uid: str) -> Path:
    base = Path(os.environ.get("HASHMM_LAYERED_MEMORY_DIR", "data/layered_memory"))
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(uid or "default"))[:64] or "default"
    d = base / safe
    (d / "scenes").mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ════════════════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════════════════
@dataclass
class Atom:
    """L1 原子事实。"""
    id: str
    content: str
    type: str = "persona"
    priority: int = 60
    scene_name: str = ""
    source_message_ids: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SceneBlock:
    """L2 情境块。"""
    name: str
    created: str = ""
    updated: str = ""
    summary: str = ""
    heat: int = 0
    content: str = ""


# ════════════════════════════════════════════════════════════════════════
# 提示词（移植自 TencentDB l1-extraction + persona-generation，中文骨架）
# ════════════════════════════════════════════════════════════════════════
_L1_SYSTEM = """你是专业的"情境切分与记忆提取专家"。分析用户对话，判断情境切换，并从中提取结构化核心记忆（仅限 persona / episodic / instruction 三类）。

【任务一 · 情境切分】结合【上一个情境】判断当前对话情境；无明显切换则沿用，出现明确换话题/新目标则切换。命名格式："AI 在和 <用户身份> 做 <目标活动>"（约 20-40 字，单句，全局唯一）。

【任务二 · 记忆提取】只从【待提取的新消息】提取，遵守：
1. 宁缺毋滥：过滤闲聊、一次性指令（"这次/本单"）与不可靠边缘信息。
2. 独立完整：脱离上下文也成立；主体以"用户（姓名）"或"AI"为核心。
3. 归纳合并：强关联/因果的多条消息合并成一条。

三类型与打分（priority 0-100，-1=极严格全局死命令）：
- persona：稳定属性/偏好/技能/习惯（住所、职业、饮食禁忌）。80-100 健康禁忌核心；50-70 一般喜好；<50 丢弃。
- episodic：客观动作/决定/计划/结果（不含纯情绪）。80-100 重要；60-70 一般；<60 丢弃。
- instruction：对 AI 的长期行为规则/格式/语气。-1 死命令；90-100 核心；70-80 重要；<70 丢弃。

【输出】仅输出合法 JSON 数组，每项为一个情境：
[{"scene_name":"...","message_ids":["..."],"memories":[{"content":"...","type":"persona|episodic|instruction","priority":80,"source_message_ids":["..."],"metadata":{}}]}]
无有效记忆也要输出情境（memories 为空数组）。不要任何 Markdown 修饰符或解释。"""

_PERSONA_SYSTEM = """你是"用户画像架构师"。结合已有 persona 与新增/变化的情境块，深度分析后输出一份 Markdown 用户画像。
要求：
- 分层小节（如 ## 稳定属性 / ## 偏好与习惯 / ## 长期指令 / ## 近期事件），每条一句、可核实、去重。
- 只保留稳定、跨会话仍成立的信息；剔除一次性/临时项。
- 已有 persona 中仍成立的内容保留并融合，冲突时以更新的情境为准（updating not creating）。
- 直接输出 Markdown 正文，不要代码块修饰符、不要解释。控制在 1200 字内。"""


# ════════════════════════════════════════════════════════════════════════
# L1：原子事实读写
# ════════════════════════════════════════════════════════════════════════
def _l1_path(uid: str) -> Path:
    return _root(uid) / "l1.jsonl"


def read_atoms(uid: str) -> list[Atom]:
    out: list[Atom] = []
    try:
        p = _l1_path(uid)
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(Atom(
                    id=str(d.get("id") or uuid.uuid4().hex[:12]),
                    content=str(d.get("content") or "")[:MAX_CONTENT_LEN],
                    type=str(d.get("type") or "persona"),
                    priority=int(d.get("priority") or 0),
                    scene_name=str(d.get("scene_name") or ""),
                    source_message_ids=list(d.get("source_message_ids") or []),
                    metadata=dict(d.get("metadata") or {}),
                    created=str(d.get("created") or ""),
                    updated=str(d.get("updated") or ""),
                ))
            except Exception:
                continue
    except Exception as e:
        log_suppressed(logger, e)
    return out


def _write_atoms(uid: str, atoms: list[Atom]) -> None:
    """整体重写（原子替换，防半写）。超容量按 priority 保留高分。"""
    try:
        keep = sorted(atoms, key=lambda a: (a.priority if a.priority >= 0 else 999,
                                            a.updated or a.created), reverse=True)[:MAX_ATOMS]
        p = _l1_path(uid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(a.to_dict(), ensure_ascii=False) for a in keep),
                       encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        log_suppressed(logger, e)


_COMMON_TOK = {
    "用户", "回答", "理由", "结论", "项目", "喜欢", "以后", "每次", "正在", "名为", "不能",
    "需要", "可以", "这个", "那个", "问题", "内容", "信息", "没有", "就是", "一个", "给出",
    "不喜", "的每", "名叫", "字是", "是李", "做一", "个名", "应该", "希望", "知道", "告诉",
}


def _salient_tokens(s: str) -> set[str]:
    """抽\"显著 token\"用于判断两条记忆是否谈同一主体：拉丁/数字串(≥3，如 HashMM/RAG/2024) +
    CJK **2 字滑窗 bigram**（跳过超常见词）。V302 从贪婪 4 字切块改为滑窗：贪婪切块对齐敏感——
    \"李雷正在\"与\"李雷\"切不出同一块，导致同主体改写错失共享 token；滑窗后 李雷 两边都在。"""
    s = s or ""
    toks: set[str] = set()
    for m in re.findall(r"[A-Za-z0-9]{3,}", s):
        toks.add(m.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", s):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _COMMON_TOK:
                toks.add(bg)
    return toks


def _llm_same_fact(llm_fn, a: str, b: str) -> bool:
    """LLM 裁决两条陈述是否\"同一事实（可能措辞不同）\"。仅用于规则判为 store 但共享显著 token 的
    边界情形。V302：收紧提示（只输出一个单词）+ 鲁棒解析——现场发现模型答\"两条陈述表达的是同一
    事实，yes\"这类前置解释导致 startswith 解析失败、该合并的没合并。失败/不确定一律 False。"""
    if not callable(llm_fn):
        return False
    sys = ("你是记忆去重助手。判断两条陈述是否表达同一条事实（允许措辞不同、详略不同、主语称呼不同）。"
           "**只输出一个单词：yes 或 no。不要输出任何其他文字、标点或解释。**")
    q = f"陈述A：{a}\n陈述B：{b}\n同一条事实吗？只回 yes 或 no。"
    try:
        try:
            raw = str(llm_fn(q, system=sys) or "")
        except TypeError:
            raw = str(llm_fn(sys + "\n\n" + q) or "")
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return False
    t = raw.strip().lower().strip("。.！!，, \n\"'")
    head = t[:12]
    # 明确否定优先（防"not the same/不是同一"被 yes 子串误吃）
    if head.startswith(("no", "否", "不是", "不同")) or "不是同一" in head:
        return False
    return head.startswith(("yes", "y", "是")) or "yes" in head or "同一" in head


def _norm_txt(s: str) -> str:
    """去标点/空白的归一化（模块级，供去重相似度用）。"""
    return re.sub(r"[\s，。、,.:：;；!！?？]", "", s or "")


# V306/V317：合并护栏（模块级，regenerate_persona 与 _dedup_decision 共用）——两条"同主体
# 高重叠"但语义相反/关键数不同时不能合并，否则会把"住在北京"与"不住在北京"、"TTL 300"与
# "TTL 600"当重复，丢掉矛盾信息。
_NEG_RE = re.compile(r"[不没無无非別别]|未|勿")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _contradicts(a: str, b: str) -> bool:
    if bool(_NEG_RE.search(a)) != bool(_NEG_RE.search(b)):
        return True                       # 否定极性不一致 → 相反
    na, nb = set(_NUM_RE.findall(a)), set(_NUM_RE.findall(b))
    if na and nb and na != nb:
        return True                       # 关键数不同
    return False


def _bigram_sim(a: str, b: str) -> float:
    """字符 2-gram 交并比（0~1）。保留字序信息，比纯字符集合 Jaccard 更能识别
    同义改写的近重复（V317；与 episodic_memory._text_sim 同族口径）。"""
    if not a or not b:
        return 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)} or {a}
    gb = {b[i:i + 2] for i in range(len(b) - 1)} or {b}
    return len(ga & gb) / max(1, len(ga | gb))


def _dedup_decision(new: Atom, existing: list[Atom]) -> tuple[str, Atom | None]:
    """轻量去重决策（不调 LLM，规则版）：返回 (action, target)。
    action ∈ store / update / skip。移植 TencentDB batchDedup 的"同主体同类合并"直觉。

    判据（对中文无分词也稳）：
      · 完全相同内容 → skip；
      · 同类型且高度重叠 → update：满足以下任一——
          (a) 一方是另一方子串；
          (b) **较短串的字符 85% 以上出现在较长串里**（对"用户喜欢结构化回答"↔"用户喜欢结构化简洁的回答"
              这类中段插入很鲁棒，纯对称 Jaccard 会因长度差把它漏掉）；
          (c) 字符集对称 Jaccard ≥ 0.72（兜底）。
    """
    def _norm(s: str) -> str:
        return re.sub(r"[\s，。、,.:：;；!！?？]", "", s or "")

    nn = _norm(new.content)
    if not nn:
        return ("skip", None)
    for e in existing:
        if e.type != new.type:
            continue
        en = _norm(e.content)
        if not en:
            continue
        if nn == en:
            return ("skip", e)
        # 相反/关键数不同 → 跳过这条候选（不合并；可能作为新事实 store）
        if _contradicts(new.content, e.content):
            continue
        if nn in en or en in nn:
            return ("update", e)
        a, b = set(nn), set(en)
        inter = a & b
        # (b) 较短串字符包含率：短串里的字符有多少落在长串里
        short = a if len(nn) <= len(en) else b
        contain = len(inter) / max(1, len(short))
        if contain >= 0.85:
            return ("update", e)
        # (c) 对称 Jaccard 兜底
        jac = len(inter) / max(1, len(a | b))
        if jac >= 0.72:
            return ("update", e)
    return ("store", None)


# ════════════════════════════════════════════════════════════════════════
# L2：情境块读写（META 分隔 Markdown）
# ════════════════════════════════════════════════════════════════════════
def _scene_file(uid: str, name: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name or "scene")[:60] or "scene"
    return _root(uid) / "scenes" / f"{safe}.md"


def _parse_scene(raw: str, name: str) -> SceneBlock:
    s = SceneBlock(name=name)
    try:
        si = raw.find(_META_START)
        ei = raw.find(_META_END)
        if si == -1 or ei == -1:
            s.content = raw.strip()
            return s
        meta = raw[si + len(_META_START):ei]
        s.content = raw[ei + len(_META_END):].strip()
        for line in meta.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k == "created":
                s.created = v
            elif k == "updated":
                s.updated = v
            elif k == "summary":
                s.summary = v
            elif k == "heat":
                try:
                    s.heat = int(v)
                except Exception:
                    s.heat = 0
    except Exception as e:
        log_suppressed(logger, e)
    return s


def _format_scene(s: SceneBlock) -> str:
    meta = "\n".join([_META_START,
                      f"created: {s.created}", f"updated: {s.updated}",
                      f"summary: {s.summary}", f"heat: {s.heat}", _META_END])
    return f"{meta}\n\n{s.content}".strip()


def read_scenes(uid: str) -> list[SceneBlock]:
    out: list[SceneBlock] = []
    try:
        d = _root(uid) / "scenes"
        if not d.exists():
            return out
        for fp in sorted(d.glob("*.md")):
            try:
                out.append(_parse_scene(fp.read_text(encoding="utf-8", errors="ignore"), fp.stem))
            except Exception:
                continue
    except Exception as e:
        log_suppressed(logger, e)
    return out


def _upsert_scene(uid: str, name: str, new_atoms: list[Atom]) -> None:
    """把该情境新提炼的原子事实并进对应情境块正文（追加要点行 + 提热度 + 更新时间）。"""
    try:
        fp = _scene_file(uid, name)
        now = _now_iso()
        if fp.exists():
            s = _parse_scene(fp.read_text(encoding="utf-8", errors="ignore"), name)
        else:
            # 容量护栏：情境过多则删最冷的
            scenes = read_scenes(uid)
            if len(scenes) >= MAX_SCENES:
                cold = min(scenes, key=lambda x: (x.heat, x.updated or x.created))
                try:
                    _scene_file(uid, cold.name).unlink()
                except Exception:
                    pass
            s = SceneBlock(name=name, created=now, summary=name[:60])
        s.updated = now
        s.heat = int(s.heat) + len(new_atoms)
        lines = [ln for ln in s.content.splitlines() if ln.strip()]
        existing = set(lines)
        for a in new_atoms:
            row = f"- [{a.type}] {a.content}".strip()
            if row not in existing:
                lines.append(row)
                existing.add(row)
        s.content = "\n".join(lines[-120:])   # 单情境正文上限，防膨胀
        fp.write_text(_format_scene(s), encoding="utf-8")
    except Exception as e:
        log_suppressed(logger, e)


# ════════════════════════════════════════════════════════════════════════
# L3：用户画像
# ════════════════════════════════════════════════════════════════════════
def _persona_path(uid: str) -> Path:
    return _root(uid) / "persona.md"


def read_persona(uid: str) -> str:
    try:
        p = _persona_path(uid)
        return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    except Exception as e:
        log_suppressed(logger, e)
        return ""


def regenerate_persona(uid: str, llm_fn, changed_scene_names: list[str] | None = None) -> dict:
    """L3 画像再生成（TencentDB persona 层）：把变化的情境块喂给 LLM 融合进已有画像。
    需要 llm_fn；无 LLM 时降级为"把所有情境 summary 汇成清单"，仍产出可读画像。"""
    try:
        scenes = read_scenes(uid)
        if not scenes:
            return {"ok": False, "detail": "无情境块可生成画像", "chars": 0}
        changed = set(changed_scene_names or [])
        changed_text = "\n\n".join(
            f"## 情境：{s.name}（heat={s.heat}）\n{s.content}"
            for s in scenes if (not changed or s.name in changed))[:6000]
        existing = read_persona(uid)
        if not callable(llm_fn):
            # 无 LLM 兜底：结构化汇总
            body = "# 用户画像（自动汇总·未接 LLM）\n\n" + "\n".join(
                f"- **{s.name}**：{s.summary or (s.content.splitlines()[0] if s.content else '')}"
                for s in sorted(scenes, key=lambda x: -x.heat)[:30])
            _persona_path(uid).write_text(body[:MAX_INJECT_LEN * 2], encoding="utf-8")
            return {"ok": True, "detail": f"汇总 {len(scenes)} 个情境（无 LLM）", "chars": len(body)}
        prompt = (f"当前时间：{_now_iso()}\n"
                  f"已有画像 persona.md：\n{existing[:3000] or '（空，首次生成）'}\n\n"
                  f"新增/变化的情境块：\n{changed_text}\n\n"
                  "请据此输出更新后的完整 persona.md 正文。")
        out = ""
        try:
            out = str(llm_fn(prompt, system=_PERSONA_SYSTEM) or "")
        except TypeError:
            out = str(llm_fn(_PERSONA_SYSTEM + "\n\n" + prompt) or "")
        out = re.sub(r"^```[a-zA-Z]*\n|```$", "", out.strip()).strip()
        if not out:
            return {"ok": False, "detail": "LLM 返回空画像", "chars": 0}
        _persona_path(uid).write_text(out[:MAX_INJECT_LEN * 2], encoding="utf-8")
        return {"ok": True, "detail": f"画像已更新（基于 {len(scenes)} 个情境）", "chars": len(out)}
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "detail": f"画像生成异常：{type(e).__name__}", "chars": 0}


# ════════════════════════════════════════════════════════════════════════
# 抽取管线：L0 消息 → L1 原子（含情境切分 + 去重）→ 顺带更新 L2 情境块
# ════════════════════════════════════════════════════════════════════════
@dataclass
class ExtractResult:
    ok: bool = False
    extracted: int = 0        # LLM 抽出的条数（去重前）
    stored: int = 0           # 实际入库（去重后新增）
    updated: int = 0          # 命中旧条更新
    scenes: list = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_l1_json(raw: str) -> list[dict]:
    """健壮解析 L1 抽取输出（容忍代码块/前后噪声）。"""
    s = re.sub(r"^```[a-zA-Z]*\n|```$", "", str(raw or "").strip()).strip()
    m = re.search(r"\[[\s\S]*\]", s)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


@_serialized_extract
def extract(uid: str, messages: list[dict], llm_fn, previous_scene: str = "") -> ExtractResult:
    """把近端对话消息抽成分层记忆。messages: [{id?, role, content, ts?}]。
    llm_fn 缺失或功能关闭 → 安全空返回（不算失败）。"""
    r = ExtractResult()
    if not enabled():
        r.detail = "分层记忆未启用（HASHMM_LAYERED_MEMORY=1 开启）"
        return r
    # 不修改调用方持有的消息对象；稳定 ID 与批次指纹让重试具有幂等语义。
    msgs = [
        dict(m) for m in (messages or [])
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ][-MAX_L0_FOR_EXTRACT:]
    if not msgs:
        r.detail = "无可抽取消息"
        return r
    if not callable(llm_fn):
        r.detail = "未配置 LLM，无法抽取"
        return r
    # 给消息补稳定 id（源头追溯用），不能用数组序号，否则滑动窗口后同一消息会换身份。
    for i, m in enumerate(msgs):
        if not str(m.get("id") or "").strip():
            seed = f"{m.get('role','user')}|{m.get('ts','')}|{m.get('content','')}"
            m["id"] = "m_" + hashlib.sha256(seed.encode("utf-8", "ignore")).hexdigest()[:12]
    batch_material = "\n".join(
        f"{m['id']}|{m.get('role','user')}|{str(m.get('content') or '').strip()}" for m in msgs
    )
    batch_fingerprint = hashlib.sha256(batch_material.encode("utf-8", "ignore")).hexdigest()
    existing = read_atoms(uid)
    if any(batch_fingerprint in list((a.metadata or {}).get("source_batch_fingerprints") or [])
           for a in existing):
        r.ok = True
        r.detail = "同一消息批次已处理，幂等跳过"
        return r
    lines = "\n".join(f"[{m['id']}|{m.get('role','user')}] {str(m['content'])[:400]}" for m in msgs)
    user_prompt = (f"【上一个情境】{previous_scene or '无'}\n"
                   f"【待提取的新消息】\n{lines}\n\n请按系统提示输出 JSON 数组。")
    raw = ""
    try:
        try:
            raw = str(llm_fn(user_prompt, system=_L1_SYSTEM) or "")
        except TypeError:
            raw = str(llm_fn(_L1_SYSTEM + "\n\n" + user_prompt) or "")
    except Exception as e:
        log_suppressed(logger, e)
        r.detail = f"LLM 抽取异常：{type(e).__name__}"
        return r
    scenes_out = _parse_l1_json(raw)
    if not scenes_out:
        r.detail = "抽取输出无法解析为情境数组（返回空，不编造）"
        return r

    now = _now_iso()
    stored = updated = extracted = 0
    scene_names: list[str] = []
    scene_to_atoms: dict[str, list[Atom]] = {}

    for seg in scenes_out:
        sname = str((seg or {}).get("scene_name") or "").strip()[:80] or (previous_scene or "默认情境")
        scene_names.append(sname)
        for mem in (seg.get("memories") or []):
            content = str((mem or {}).get("content") or "").strip()[:MAX_CONTENT_LEN]
            mtype = str((mem or {}).get("type") or "persona").strip().lower()
            if mtype not in ATOM_TYPES:
                mtype = "persona"
            try:
                prio = int((mem or {}).get("priority", 0))
            except Exception:
                prio = 0
            if not content:
                continue
            extracted += 1
            # 宁缺毋滥：低分丢弃（-1 是死命令要保留）
            floor = {"persona": 50, "episodic": 60, "instruction": 70}[mtype]
            if prio != -1 and prio < floor:
                continue
            atom = Atom(id=uuid.uuid4().hex[:12], content=content, type=mtype,
                        priority=prio, scene_name=sname,
                        source_message_ids=list((mem or {}).get("source_message_ids") or []),
                        metadata=dict((mem or {}).get("metadata") or {}),
                        created=now, updated=now)
            atom.metadata["source_batch_fingerprints"] = [batch_fingerprint]
            action, target = _dedup_decision(atom, existing)
            # V299 去重召回增强：规则判 store，但存在**同类型且共享显著 token**（如都提到 HashMM/李雷）
            # 的旧原子时，用 LLM 裁决是否\"同一事实的改写\"——是则并入旧条（update），根治\"同段对话再抽
            # 一次、措辞变了就重复堆积\"。仅对边界情形调用 LLM，且判 no 不合并，绝不错并同主体的不同事实。
            if action == "store":
                new_tok = _salient_tokens(atom.content)
                for e in existing:
                    if e.type != atom.type:
                        continue
                    if _contradicts(atom.content, e.content):
                        continue          # 矛盾护栏优先，永不合并
                    shared = new_tok & _salient_tokens(e.content) if new_tok else set()
                    # V302：≥2 共享显著 token = 强证据直接合并（不依赖 LLM）。
                    if len(shared) >= 2:
                        action, target = "update", e
                        break
                    # V317：恰好 1 个共享 token，或【2-gram 相似度落在模糊区 0.45~0.72】
                    #（字符集合判据够不着、但可能是同义改写），请 LLM 裁决是否同一事实。
                    # 根治"同段对话措辞变了再抽一次就重复堆积"里 token 不共享的漏网情形。
                    sim = _bigram_sim(_norm_txt(atom.content), _norm_txt(e.content))
                    if (len(shared) == 1 or 0.45 <= sim < 0.72) and _llm_same_fact(
                            llm_fn, atom.content, e.content):
                        action, target = "update", e
                        break
            if action == "skip":
                if target is not None:
                    fps = list((target.metadata or {}).get("source_batch_fingerprints") or [])
                    if batch_fingerprint not in fps:
                        target.metadata = dict(target.metadata or {})
                        target.metadata["source_batch_fingerprints"] = fps + [batch_fingerprint]
                        target.updated = now
                        updated += 1
                continue
            if action == "update" and target is not None:
                # 用更高优先级 + 更长内容覆盖旧条
                if atom.priority == -1 or len(atom.content) >= len(target.content) or atom.priority > target.priority:
                    target.content = atom.content
                    target.priority = atom.priority if atom.priority != 0 else target.priority
                    target.updated = now
                    target.scene_name = sname
                fps = list((target.metadata or {}).get("source_batch_fingerprints") or [])
                if batch_fingerprint not in fps:
                    target.metadata = dict(target.metadata or {})
                    target.metadata["source_batch_fingerprints"] = fps + [batch_fingerprint]
                updated += 1
                scene_to_atoms.setdefault(sname, []).append(target)
            else:
                existing.append(atom)
                stored += 1
                scene_to_atoms.setdefault(sname, []).append(atom)

    if stored or updated:
        _write_atoms(uid, existing)
    for sname, atoms in scene_to_atoms.items():
        _upsert_scene(uid, sname, atoms)

    r.ok = True
    r.extracted = extracted
    r.stored = stored
    r.updated = updated
    r.scenes = scene_names
    r.detail = f"抽 {extracted} 条 → 新增 {stored}、更新 {updated}；情境 {len(set(scene_names))} 个"
    return r


# ════════════════════════════════════════════════════════════════════════
# 召回：渐进式披露（顶层 Persona 优先，命中关键词再下钻 L1 原子）
# ════════════════════════════════════════════════════════════════════════
def recall(uid: str, query: str, limit: int = 12) -> list[dict]:
    """分层召回：先给 Persona 摘要（顶层结构），再按关键词重叠从 L1 原子里下钻细节。
    返回统一 {kind, source, text, score, ts, meta}，供 memory/hub 联邦排序。"""
    q = (query or "").strip()
    out: list[dict] = []
    if not enabled():
        return out
    try:
        # 顶层：画像（如有）——作为一条高置信"背景"注入
        persona = read_persona(uid)
        if persona.strip():
            out.append({
                "kind": "persona", "source": "分层画像(L3)",
                "text": persona.strip()[:MAX_INJECT_LEN], "score": 0.9, "ts": 0,
                "meta": {"layer": "L3"},
            })
    except Exception as e:
        log_suppressed(logger, e)
    try:
        atoms = read_atoms(uid)
        if not atoms:
            return out[:limit]
        # 中文无空格分词——用「字符集重叠」为主、词级重叠为辅，两者都算，取更强信号。
        def _chars(s: str) -> set:
            return set(re.sub(r"[\s，。、,.:：;；!！?？]", "", s or ""))

        def _words(s: str) -> set:
            return set(w for w in re.sub(r"[\s，。、,.:：;；!！?？]", " ", s or "").split() if w)

        qchars = _chars(q)
        qwords = _words(q)
        scored: list[tuple[float, Atom]] = []
        for a in atoms:
            achars = _chars(a.content)
            awords = _words(a.content)
            # 字符重叠率：查询字符里有多少落在该原子里（对中文最实用）
            char_overlap = (len(qchars & achars) / max(1, len(qchars))) if qchars else 0.0
            word_overlap = len(qwords & awords)
            rel = max(char_overlap, min(1.0, 0.34 * word_overlap))   # 两通道取强者
            # instruction/persona 天然更该被记住（基线分更高）；相关性再加分
            base = {"instruction": 0.5, "persona": 0.45, "episodic": 0.4}.get(a.type, 0.38)
            if a.priority == -1:
                base = 0.95
            s = base + 0.45 * rel + 0.0012 * max(0, a.priority)
            scored.append((min(1.0, s), a))
        scored.sort(key=lambda x: x[0], reverse=True)
        for s, a in scored[:limit]:
            out.append({
                "kind": "atom", "source": f"分层原子(L1·{a.type})",
                "text": a.content, "score": round(s, 4),
                "ts": 0,
                "meta": {"layer": "L1", "atom_type": a.type, "priority": a.priority,
                         "scene": a.scene_name, "id": a.id},
            })
    except Exception as e:
        log_suppressed(logger, e)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def inject_block(uid: str, query: str = "", max_len: int = MAX_INJECT_LEN) -> str:
    """给系统提示注入的分层记忆块（Persona 摘要 + Top 指令/画像原子），受长度护栏约束。"""
    if not enabled():
        return ""
    parts: list[str] = []
    try:
        persona = read_persona(uid).strip()
        if persona:
            parts.append("【长期画像】\n" + persona[:max_len // 2])
    except Exception as e:
        log_suppressed(logger, e)
    try:
        hits = recall(uid, query, limit=10)
        atoms = [h for h in hits if h.get("kind") == "atom"]
        if atoms:
            lines = "\n".join(f"- {h['text']}" for h in atoms[:8])
            parts.append("【相关记忆】\n" + lines)
    except Exception as e:
        log_suppressed(logger, e)
    block = "\n\n".join(parts).strip()
    return block[:max_len]


def stats(uid: str) -> dict:
    """快照：各层条数 + 情境热度 Top。"""
    d = {"enabled": enabled(), "atoms": 0, "scenes": 0, "persona_chars": 0,
         "by_type": {}, "top_scenes": []}
    try:
        atoms = read_atoms(uid)
        d["atoms"] = len(atoms)
        for a in atoms:
            d["by_type"][a.type] = d["by_type"].get(a.type, 0) + 1
    except Exception as e:
        log_suppressed(logger, e)
    try:
        scenes = read_scenes(uid)
        d["scenes"] = len(scenes)
        d["top_scenes"] = [{"name": s.name, "heat": s.heat, "summary": s.summary}
                           for s in sorted(scenes, key=lambda x: -x.heat)[:5]]
    except Exception as e:
        log_suppressed(logger, e)
    try:
        d["persona_chars"] = len(read_persona(uid))
    except Exception as e:
        log_suppressed(logger, e)
    return d

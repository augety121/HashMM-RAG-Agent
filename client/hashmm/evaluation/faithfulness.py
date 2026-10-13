"""hashmm/evaluation/faithfulness.py — 忠实度合约（B1，V103.90）。

把"引用接地"从『编号没越界』(loop.py::_citation_issues) 升级到
『每条事实声明都引用了、且引用的证据真能支撑它』。对标 RAGAS 的 faithfulness 维度
与企业（法律/医疗/金融）最在意的"每句可溯源"。

核心是纯函数 ``audit_faithfulness(answer, sources)``：

  1. 把终答按中英文句末切句（剥离代码块/标题/列表标记等噪声）；
  2. 对每个"事实型"句子（含数据数字 / 比较级 / 实体特征）要求带 ``[N]`` 角标；
     无角标 → ``uncited``；
  3. 对带角标的事实句：取本轮检索证据池做轻量"支撑判定"（中文 2-gram + 英文词的
     词法重叠，与 loop._lex_overlap 同口径）；过弱的少数句子可选地升级到**一次**
     LLM judge 兜底确认；接不回证据 → ``unsupported``。

返回结构化 ``FaithfulnessReport``，三处复用：
  - agent loop 的忠实度门：有 ``unsupported`` / 多条 ``uncited`` → 注入一次修正指引；
  - streaming 直答路径：把未接地声明挂到 ``done`` 事件，前端标灰 / 加 ⚠️；
  - agent_bench 的 faithfulness 评测维度（用数字证明改动有效）。

设计取向 —— **刻意"低假阳性"**（避免无谓触发重写、避免打扰用户）：
  · 只对**清晰的**事实句要求引用（裸单数字、枚举、元话语句一律豁免）；
  · 支撑阈值取宽，数值/词法两层，judge 仅对"近乎零重叠"的少数句兜底；
  · LLM judge **可注入**，缺省时纯词法判定 —— 故本模块**零外部依赖、沙箱可充分单测**。

所有函数纯逻辑、无 IO；阈值可经环境变量覆盖，便于真机调参。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional


# ─────────────────────────── 可调参数（环境变量可覆盖）───────────────────────────
def _envf(name: str, default: float) -> float:
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


def _envi(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v not in (None, "") else default
    except Exception:
        return default


# 句子词法重叠 ≥ 此值即判"有支撑"（chunk 长、句子短，取宽以压假阳性）。
SUPPORT_THRESHOLD = _envf("HASHMM_FAITHFULNESS_SUPPORT", 0.20)
# 无 judge 时，重叠低于 SUPPORT_THRESHOLD * 此系数（近乎零重叠）才直接判 unsupported；
# 介于二者之间的"灰区"给予 benefit-of-the-doubt（判为支撑），把误伤降到最低。
WEAK_FACTOR = _envf("HASHMM_FAITHFULNESS_WEAK_FACTOR", 0.4)
# 一次审计最多升级几句到 LLM judge（控成本/延迟）。
MAX_JUDGE_CALLS = _envi("HASHMM_FAITHFULNESS_MAX_JUDGE", 4)

# 句首元话语前缀（这些句不算事实声明，豁免引用要求）。
_META_PREFIXES = (
    "综上", "总之", "总而言之", "首先", "其次", "再次", "最后", "此外", "另外",
    "因此", "所以", "下面", "以下", "如下", "如上", "上述", "需要注意", "请注意",
    "值得注意", "简而言之", "换句话说", "具体来说", "举例", "例如", "比如说",
    "根据知识库", "根据检索", "根据以上", "根据上述", "根据文档", "基于以上",
    "基于检索", "基于上述", "总的来说", "整体而言", "概括来说", "小结",
)

# 比较/程度/趋势类标记 —— 出现则该句很可能是可核查的事实主张。
_COMPARATIVE = (
    "最", "更", "比", "超过", "低于", "高于", "达到", "达成", "提升", "提高",
    "下降", "降低", "增长", "增加", "减少", "相比", "优于", "领先", "突破",
    "高达", "仅为", "约为", "大于", "小于", "不低于", "不超过", "翻倍", "倍",
    "increase", "decrease", "higher", "lower", "most", "best", "largest",
    "smallest", "greater", "exceed", "reach", "improv", "reduc", "grow",
)

# 数据单位 —— 数字紧跟这些单位时视作"数据数字"（而非枚举/序号）。
_DATA_UNITS = (
    "%", "‰", "％", "年", "月", "日", "万", "亿", "千", "百", "元", "美元",
    "人", "次", "条", "个百分点", "倍", "ms", "毫秒", "秒", "分钟", "小时",
    "天", "周", "月", "gb", "mb", "kb", "tb", "g", "m", "km", "cm", "mm",
    "k", "w", "瓦", "度", "℃", "°c", "kg", "吨", "件", "套", "台", "款",
)

# 独立 [N] 引用：排除 markdown 链接 [N](url) 与 ASCII 数组下标 a[0]。
# 注意：边界用 ASCII-only 字符类（不用 \w）——因为 Python 的 \w 在 Unicode 模式下
# **包含中文字符**，会把"营收26.7亿[1]"这种中文句末引用误排除（loop._citation_issues
# 原版正是用了 \w，故在中文正文里基本抓不到句中引用——这里予以修正，正确支持中文）。
_CITE_RE = re.compile(r"(?<![A-Za-z0-9_\]])\[(\d{1,3})\](?!\()")

# ── V306 strict 硬事实抽取（默认路径不用，仅 audit_faithfulness(strict_numbers=True) 用）──
# 目的：把"引用了来源、却断言一个证据里根本没有的具体硬事实"的句子抓出来——这是最典型的
# 幻觉签名（数字对不上 / 凭空造年份 / Big-O 复杂度写反）。刻意只认**高辨识度硬 token**，
# 避免误伤（裸单数字、枚举、口径改写已由 _data_numbers 的保守规则挡掉）。
_BIGO_RE = re.compile(r"O\(\s*(?:1|n(?:\s*log\s*n)?|log\s*n|n\^?2|n²|n\s*\*\s*m|m\s*\+\s*n)\s*\)", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")          # 1800–2099 的年份
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*[%‰％]")                   # 百分比/千分比


def _norm_token(t: str) -> str:
    """归一化硬 token 便于子串匹配：去空白、小写、Big-O 去内部空格。"""
    return re.sub(r"\s+", "", str(t or "")).lower()


def hard_facts(sentence: str) -> list[str]:
    """抽取句中"高辨识度硬事实"token：Big-O 复杂度 / 年份 / 百分比 / 多位数据数字。
    比 _data_numbers 多认 Big-O（O(1)/O(log n) 等），供 strict 数字接地判定用。
    """
    s = _CITE_RE.sub(" ", str(sentence or ""))
    out: list[str] = []
    out += [m.group(0) for m in _BIGO_RE.finditer(s)]
    out += [m.group(0) for m in _YEAR_RE.finditer(s)]
    out += [m.group(0) for m in _PERCENT_RE.finditer(s)]
    out += _data_numbers(s)          # 复用既有保守数据数字（≥2 位 / 带单位）
    # 去重（按归一化形态）
    seen, uniq = set(), []
    for t in out:
        k = _norm_token(t)
        if k and k not in seen:
            seen.add(k); uniq.append(t)
    return uniq


def _hard_fact_absent(sentence: str, evidence_texts: list[str]) -> list[str]:
    """返回句中"在证据里找不到"的硬事实 token 列表（空=全部有据）。
    匹配做归一化：证据去空白小写、Big-O 去内部空格、百分比可退化到纯数字比对。
    """
    facts = hard_facts(sentence)
    if not facts:
        return []
    norm_ev = [_norm_token(c) for c in evidence_texts]
    missing: list[str] = []
    for f in facts:
        k = _norm_token(f)
        digits = re.match(r"[\d.]+", k)
        k2 = digits.group(0) if digits else ""
        if not any((k in c) or (k2 and k2 in c) for c in norm_ev):
            missing.append(f)
    return missing


# ─────────────────────────── 切句 / 分词（与 loop._lex_overlap 同口径）───────────────────────────
def strip_noise(text: str) -> str:
    """剥离代码块、行内代码、markdown 标题/分隔/列表标记，避免把代码/标题误判为事实句。"""
    s = text or ""
    s = re.sub(r"```.*?```", " ", s, flags=re.S)          # 围栏代码块
    s = re.sub(r"`[^`]*`", " ", s)                          # 行内代码
    s = re.sub(r"^\s{0,3}#{1,6}\s+", "", s, flags=re.M)     # 标题井号
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)          # 无序列表项标记
    s = re.sub(r"^\s*\d+[\.\)、]\s+", "", s, flags=re.M)    # 有序列表项序号（行首）
    s = re.sub(r"^\s*[|>].*$", "", s, flags=re.M)            # 表格行 / 引用块
    return s


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点 + 换行切句。保留句内的 [N] 角标。"""
    s = strip_noise(text)
    # 在句末标点后插入切分点；中文标点本身不消失（保留语义），英文句点需后接空白才切（避小数/缩写误切）。
    s = re.sub(r"([。！？；])", r"\1\n", s)
    s = re.sub(r"([.!?])(\s)", r"\1\n\2", s)
    out: list[str] = []
    for line in s.split("\n"):
        seg = line.strip()
        if seg:
            out.append(seg)
    return out


def tokenize(text: str) -> set[str]:
    """中文 2-gram（含单字）+ 英文/数字词。与 loop._lex_overlap 完全同口径，保证一致性。"""
    t = (text or "").lower()
    out: set[str] = set()
    for m in re.findall(r"[a-z0-9_]{2,}", t):
        out.add(m)
    for seg in re.findall(r"[\u4e00-\u9fff]+", t):
        if len(seg) == 1:
            out.add(seg)
        for i in range(len(seg) - 1):
            out.add(seg[i:i + 2])
    return out


# ─────────────────────────── 事实句判定 ───────────────────────────
def _data_numbers(sentence: str) -> list[str]:
    """抽取句中的"数据数字"——剔除 [N] 角标后，满足下列任一即算数据：
       · 含小数点（3.14）；· 连续 ≥2 位数字（26 / 5576 / 2024）；
       · 单/多位数字紧跟数据单位（90% / 3倍 / 2024年）。
    裸单数字（"分3步"中的 3）不算，避免把枚举误判为数据。
    """
    s = _CITE_RE.sub(" ", sentence)
    nums: list[str] = []
    # 小数 或 ≥2 位整数（允许千分位逗号）
    for m in re.findall(r"\d[\d,]*\.\d+|\d{2,}(?:,\d{3})*", s):
        nums.append(m)
    # 单/多位数字 + 数据单位
    low = s.lower()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([%‰％]|[\u4e00-\u9fffa-z℃°]+)", low):
        unit = m.group(2)
        if any(unit.startswith(u) or u.startswith(unit) for u in _DATA_UNITS):
            nums.append(m.group(0))
    return nums


def is_factual_claim(sentence: str) -> bool:
    """判定一个句子是否为"需要引用接地"的事实主张。

    取向偏保守（宁可漏判不滥判），满足任一即视为事实句：
      · 含数据数字（见 _data_numbers）；
      · 含比较/趋势类标记（最/更/超过/提升/倍 …）；
      · 含书名号/引号包裹的具名实体（《…》/“…”）。
    豁免：过短句、句首元话语（综上/首先/根据检索…）、纯问句。
    """
    seg = (sentence or "").strip()
    if len(seg) < 8:
        return False
    if seg.endswith(("?", "？")):                 # 问句不是主张
        return False
    bare = _CITE_RE.sub("", seg).strip()
    if len(bare) < 6:
        return False
    for p in _META_PREFIXES:
        if bare.startswith(p):
            return False
    if _data_numbers(seg):
        return True
    low = bare.lower()
    if any(k in bare or k in low for k in _COMPARATIVE):
        return True
    if re.search(r"《[^》]{1,40}》|“[^”]{2,40}”", bare):
        return True
    return False


def extract_citations(sentence: str) -> list[int]:
    """抽取句中独立 [N] 角标编号（升序去重）。"""
    try:
        return sorted({int(m) for m in _CITE_RE.findall(sentence or "")})
    except Exception:
        return []


# ─────────────────────────── 证据池 / 支撑判定 ───────────────────────────
def normalize_sources(sources) -> list[str]:
    """把多种来源形态统一成"证据文本列表"。

    支持：① dict（含 text/content/snippet 字段，streaming 的 rag_sources）；
         ② 纯字符串（loop 解析出的 chunk 文本）；③ 已是文本列表。
    """
    out: list[str] = []
    for s in (sources or []):
        if isinstance(s, str):
            t = s
        elif isinstance(s, dict):
            t = s.get("text") or s.get("content") or s.get("snippet") or s.get("body") or ""
        else:
            t = str(s or "")
        t = (t or "").strip()
        if t:
            out.append(t)
    return out


# 行首 [N] 证据块标记（仅匹配数字编号，故 "[多查询融合]"/"[深度检索答案]" 之类不被命中）。
_BLOCK_RE = re.compile(r"(?m)^\[(\d{1,3})\][ \t]")


def parse_numbered_evidence(result_text: str) -> list[str]:
    """从工具结果文本里抽取以 ``[N]`` 开头的证据块（每块=该编号到下一编号之间的全文）。

    适配两种真实格式：
      · kb_search / pack_sources： ``[1] [文件 p.页] (相关度:x)\\n正文…``
      · deep_search：             ``[1] (文件) 正文`` （前面的【深度检索答案】行不以 [N] 起，自然排除）

    返回各块文本（含其头行，词法判定无碍）。无 [N] 块时返回空。纯函数可单测。
    """
    s = result_text or ""
    marks = list(_BLOCK_RE.finditer(s))
    if not marks:
        return []
    blocks: list[str] = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
        seg = s[start:end].strip()
        if seg:
            blocks.append(seg)
    return blocks


def lexical_support(sentence: str, evidence_texts: list[str]) -> float:
    """句子相对证据池的最大词法重叠率（0~1）。

    重叠率 = |句 token ∩ chunk token| / |句 token|，取所有 chunk 的最大值。
    句子无有效 token 时返回 1.0（无从判定 → 按"有支撑"，不误伤）。
    """
    q = tokenize(_CITE_RE.sub(" ", sentence))
    if not q:
        return 1.0
    best = 0.0
    for chunk in evidence_texts:
        r = tokenize(chunk)
        if not r:
            continue
        ov = len(q & r) / len(q)
        if ov > best:
            best = ov
            if best >= 0.999:
                break
    return best


def _numbers_present_in_evidence(sentence: str, evidence_texts: list[str]) -> Optional[bool]:
    """句中数据数字是否能在证据里找到（做归一化：去千分位逗号/空白后子串匹配）。

    返回 True=全部找到 / False=有数字找不到 / None=句中无数据数字（不适用）。
    仅作**辅助降权**信号，不单独定罪（避免 "5,576" vs "5576"、"约5600" 之类格式差异误伤）。
    """
    nums = _data_numbers(sentence)
    if not nums:
        return None
    norm_ev = [re.sub(r"[,\s]", "", c) for c in evidence_texts]
    for n in nums:
        key = re.sub(r"[,\s]", "", n)
        # 去掉尾随单位后取纯数字串再匹配（90% → 90）
        digits = re.match(r"[\d.]+", key)
        key2 = digits.group(0) if digits else key
        if not any((key in c) or (key2 and key2 in c) for c in norm_ev):
            return False
    return True


# ─────────────────────────── 报告结构 ───────────────────────────
@dataclass
class FaithfulnessReport:
    """忠实度审计结果。``ok`` 为 True 表示无未引用且无未接地的事实句。"""
    checked: bool = False                       # 是否真正跑了审计（无证据则 False）
    ok: bool = True
    total_factual: int = 0
    supported: int = 0
    uncited: list = field(default_factory=list)        # [{"idx","sentence"}]
    unsupported: list = field(default_factory=list)    # [{"idx","sentence","cited","overlap"}]
    number_unsupported: list = field(default_factory=list)  # V306 strict：[{"idx","sentence","missing"}]
    judge_calls: int = 0

    @property
    def ratio(self):
        """已接地事实句占比。V308：无事实句 / 未审计 → None（不可评估），
        不再返回 1.0。看板据 status 决定是否计入平均，避免"无从判断"被当满分。"""
        if not self.checked or self.total_factual == 0:
            return None
        return self.supported / self.total_factual

    @property
    def status(self) -> str:
        """三态结论：passed / failed / not_evaluable。
        · not_evaluable：没跑审计（无证据）或没有事实句可判 —— 排除出通过率分母。
        · passed：所有事实句都已接地且无未引用。
        · failed：存在未引用或未接地的事实句。
        """
        if not self.checked or self.total_factual == 0:
            return "not_evaluable"
        return "passed" if self.ok else "failed"

    def to_dict(self) -> dict:
        r = self.ratio
        return {
            "checked": self.checked,
            "ok": self.ok,
            "status": self.status,                      # V308 三态
            "total_factual": self.total_factual,
            "supported": self.supported,
            "ratio": round(r, 4) if r is not None else None,
            "uncited": self.uncited,
            "unsupported": self.unsupported,
            "number_unsupported": self.number_unsupported,
            "judge_calls": self.judge_calls,
        }


# ─────────────────────────── 主审计函数 ───────────────────────────
def audit_faithfulness(
    answer: str,
    sources,
    *,
    support_threshold: float = SUPPORT_THRESHOLD,
    judge_fn: Optional[Callable[[str, str], Optional[bool]]] = None,
    max_judge_calls: int = MAX_JUDGE_CALLS,
    strict_numbers: bool = False,
) -> FaithfulnessReport:
    """对终答做忠实度审计。

    参数
        answer: 终答文本。
        sources: 本轮检索证据（dict / str / 文本列表均可，见 normalize_sources）。
        support_threshold: 词法重叠 ≥ 此值判"有支撑"。
        judge_fn: 可选。签名 ``(sentence, evidence_text) -> Optional[bool]``，
                  返回 True=支撑 / False=不支撑 / None=judge 不可用（按支撑处理，不误伤）。
                  缺省 None 时纯词法判定（沙箱可充分单测）。
        max_judge_calls: 单次审计升级到 judge 的句子数上限（控成本）。

    返回 ``FaithfulnessReport``。当无证据时返回 ``checked=False, ok=True``
    （无依据 → 不判定，与 _citation_issues 在 max<=0 时返回空一致）。
    """
    rep = FaithfulnessReport()
    evidence = normalize_sources(sources)
    if not (answer or "").strip() or not evidence:
        return rep                              # 无证据/空答案：不判定
    rep.checked = True

    weak_floor = support_threshold * WEAK_FACTOR
    judged = 0
    ev_join = "\n\n".join(evidence)[:4000]      # judge 时给的证据上下文（控长度）

    for idx, sent in enumerate(split_sentences(answer)):
        cites = extract_citations(sent)
        # V306：硬事实缺据检测独立于 is_factual_claim —— 只含 Big-O 复杂度这类句子，
        # 启发式不判其为"事实句"，但若它引用了来源却断言证据里没有的复杂度，同样是幻觉。
        missing_hard = _hard_fact_absent(sent, evidence) if cites else []
        if not is_factual_claim(sent) and not missing_hard:
            continue
        rep.total_factual += 1
        if not cites:
            rep.uncited.append({"idx": idx, "sentence": sent[:160]})
            continue

        # ── 硬事实接地：引用了来源却断言证据里没有的硬事实（数字对不上 / 造年份 / Big-O
        # 写反）→ 记入 number_unsupported。默认 strict_numbers=False 时只记录不定罪
        # （不改变既有 supported/ok 语义与历史测试）；strict_numbers=True 时按未接地处理。
        if missing_hard:
            rep.number_unsupported.append({"idx": idx, "sentence": sent[:160],
                                           "cited": cites, "missing": missing_hard[:6]})
            if strict_numbers:
                rep.unsupported.append({"idx": idx, "sentence": sent[:160],
                                        "cited": cites, "overlap": None,
                                        "reason": "hard_fact_absent", "missing": missing_hard[:6]})
                continue

        overlap = lexical_support(sent, evidence)
        nums_ok = _numbers_present_in_evidence(sent, evidence)

        if overlap >= support_threshold and nums_ok is not False:
            rep.supported += 1
            continue

        # 灰区：词法弱 或 数字对不上 —— 先尝试 judge 兜底，否则按宽松规则定夺。
        verdict: Optional[bool] = None
        if judge_fn is not None and judged < max_judge_calls:
            try:
                verdict = judge_fn(sent, ev_join)
            except Exception:
                verdict = None
            judged += 1

        if verdict is True:
            rep.supported += 1
        elif verdict is False:
            rep.unsupported.append({"idx": idx, "sentence": sent[:160],
                                    "cited": cites, "overlap": round(overlap, 3)})
        else:
            # 无 judge / judge 不可用：仅"近乎零重叠 或 (低重叠且数字对不上)"才定罪，
            # 其余灰区给 benefit-of-the-doubt，最大限度压低假阳性。
            if overlap < weak_floor or (overlap < support_threshold and nums_ok is False):
                rep.unsupported.append({"idx": idx, "sentence": sent[:160],
                                        "cited": cites, "overlap": round(overlap, 3)})
            else:
                rep.supported += 1

    rep.judge_calls = judged
    rep.ok = (not rep.uncited) and (not rep.unsupported)
    return rep


# ─────────────────────────── 门控决策（供 loop 忠实度门用）───────────────────────────
def should_request_revision(rep: FaithfulnessReport, *,
                            min_unsupported: int = 1, min_uncited: int = 2) -> bool:
    """是否值得触发一次"补来源/改写"修正。

    取向：只对**真问题**开火 —— 出现 ≥min_unsupported 条接不回证据的声明（真幻觉），
    或 ≥min_uncited 条完全无引用的事实句。长答案里偶有一句没标号是可容忍的，不触发重写。
    """
    if not rep.checked:
        return False
    if len(rep.unsupported) >= max(1, min_unsupported):
        return True
    if len(rep.uncited) >= max(1, min_uncited):
        return True
    return False


def build_revision_instruction(rep: FaithfulnessReport, *, max_items: int = 6) -> str:
    """生成给模型的一次性修正指引（中文，与 loop 现有 verify/DoD/citation 门同风格）。"""
    lines = ["⚠️ 忠实度校验：以下事实声明未能可靠接地，请逐条修正后再给最终回答——"]
    n = 0
    for it in rep.unsupported:
        if n >= max_items:
            break
        lines.append(f"· 第 {it['idx'] + 1} 句「{it['sentence']}」引用了 "
                     f"{['[%d]' % c for c in it.get('cited', [])]}，但所引证据并不支持该说法。"
                     "请改引真正支持它的来源编号；若检索结果里确无依据，改写为"
                     "「据现有资料无法确认」或删除该句。")
        n += 1
    for it in rep.uncited:
        if n >= max_items:
            break
        lines.append(f"· 第 {it['idx'] + 1} 句「{it['sentence']}」是事实性陈述但没有标注来源。"
                     "请在句末补上对应的来源角标 [N]；无来源支撑的请删除或明确标注为推断。")
        n += 1
    lines.append("注意：不要编造检索结果里不存在的出处；只用真实存在的来源编号。")
    return "\n".join(lines)


def summarize_for_ui(rep: FaithfulnessReport) -> dict:
    """给前端的精简载荷：未接地声明清单 + 一句话结论（用于标灰/加 ⚠️）。"""
    if not rep.checked:
        return {"checked": False}
    items = []
    for it in rep.unsupported:
        items.append({"idx": it["idx"], "text": it["sentence"], "kind": "unsupported"})
    for it in rep.uncited:
        items.append({"idx": it["idx"], "text": it["sentence"], "kind": "uncited"})
    _r = rep.ratio
    return {
        "checked": True,
        "ok": rep.ok,
        "status": rep.status,                                  # V308 三态
        "grounded_ratio": round(_r, 3) if _r is not None else None,
        "total_factual": rep.total_factual,
        "flagged": items,
    }

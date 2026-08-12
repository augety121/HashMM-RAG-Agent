"""Query Planner v6.0 — decompose complex queries into retrieval plans.

Handles:
- Multi-entity comparison ("对比小米和腾讯的营收")
- Time-series queries ("2021-2024年的增长趋势")
- Multi-aspect queries ("营收、利润和毛利率分别是多少")
- Aggregation ("各子公司的收入汇总")

Uses rules (no LLM) for 80% of cases. Falls back to single search for simple queries.

Usage:
    planner = QueryPlanner()
    plan = planner.plan("对比小米和腾讯2024年的营收和利润")
    # → [SearchStep("小米 2024 营收 利润"), SearchStep("腾讯 2024 营收 利润")]
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from hashmm.utils import get_logger

logger = get_logger("hashmm.query_planner")

# ── Entity extraction patterns ──
_COMPANY_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6}(?:集团|公司|控股|银行|证券|科技|汽车))|'
    r'(小米|腾讯|阿里|百度|华为|字节|京东|美团|拼多多|网易|'
    r'比亚迪|蔚来|理想|特斯拉|苹果|三星|微软|谷歌|亚马逊|Meta)'
)
_TIME_RE = re.compile(
    r'(20\d{2}(?:\s*[-~到至]\s*20\d{2})?(?:年)?|'
    r'(?:第?[一二三四1-4]季度|Q[1-4]|上半年|下半年|H[12]))',
    re.IGNORECASE,
)
_METRIC_RE = re.compile(
    r'(营收|营[业收]?收入|净利润|毛利[润率]?|研发[费投]入|'
    r'总?资产|负债|现金流|出货量|用户数|市值|股价|'
    r'ROE|ROA|EPS|GMV|收入|利润|成本|费用)',
    re.IGNORECASE,
)
_COMPARE_RE = re.compile(r'(对比|比较|vs|VS|区别|差异|哪个|谁的)')
_TREND_RE = re.compile(r'(趋势|变化|增长|下降|走势|历年|各年)')
_MULTI_ASPECT_RE = re.compile(r'(和|及|以及|、|跟|与).{0,4}(分别|各|都)')
_TIME_RANGE_RE = re.compile(r'(20\d{2})\s*[-~到至]\s*(20\d{2})')

# ── General leading-entity fallback (NO whitelist) ──
# When _COMPANY_RE finds nothing (e.g. a private-KB entity like 银河物流 / 鲜达冷链
# that doesn't end in 公司/集团 and isn't a famous brand), take the leading CJK noun
# phrase as the entity. Without this, timeline/comparison plans got entity="" → the
# per-year sub-queries dropped the subject → wrong chunks retrieved → wrongful refusal.
_ENTITY_STOP = ("资料", "文档", "知识库", "这家", "那家", "某公司", "某家", "某个",
                "本文", "去年", "今年", "明年", "前年", "今天", "明天", "我们", "你们")
_LEAD_FILLER = ("把", "请问", "问一下", "请", "帮我查一下", "帮我查", "帮我",
                "查一下", "关于", "看一下", "看下", "那个", "那")


def _leading_entity(query: str) -> str:
    """Best-effort entity = leading CJK run (2–8 chars) after stripping fillers.
    Returns '' for non-entity leads (资料/文档/某公司/去年…) or metric words."""
    s = (query or "").strip()
    for f in _LEAD_FILLER:
        if s.startswith(f):
            s = s[len(f):].lstrip()
            break
    m = re.match(r'([\u4e00-\u9fff]{2,8})', s)
    if not m:
        return ""
    ent = m.group(1)
    if any(ent.startswith(w) for w in _ENTITY_STOP):
        return ""
    if _METRIC_RE.match(ent):  # leading word is itself a metric, not an entity
        return ""
    return ent


@dataclass
class SearchStep:
    """A single search to execute."""
    query: str
    label: str = ""        # Human-readable label for this step
    entity: str = ""       # Which entity this search is for
    time_period: str = ""  # Which time period
    merge_key: str = ""    # Key for merging results across steps


@dataclass
class ExecutionPlan:
    """A plan of searches to execute."""
    steps: list[SearchStep] = field(default_factory=list)
    merge_type: str = "union"  # "union" | "comparison" | "timeline"
    output_hint: str = ""      # Hint for LLM output format

    @property
    def is_multi_step(self) -> bool:
        return len(self.steps) > 1


class QueryPlanner:
    """Decompose complex queries into multi-step retrieval plans.

    Rules-based (no LLM call). Covers:
    1. Multi-entity comparison → separate searches per entity
    2. Time range → separate searches per year/period
    3. Multi-aspect → single search with all aspects (LLM handles extraction)
    4. Simple query → single search (no decomposition)
    """

    def plan(self, query: str, history: list[dict] | None = None) -> ExecutionPlan:
        """Generate an execution plan for the query.

        Args:
            query: User query
            history: Conversation history (for context)

        Returns:
            ExecutionPlan with 1+ SearchSteps
        """
        # Extract components
        companies_raw = _COMPANY_RE.findall(query)
        # Flatten groups: each match returns (group1, group2), pick non-empty
        companies = list(set(
            g1 or g2 for g1, g2 in companies_raw if g1 or g2
        ))
        times = list(set(_TIME_RE.findall(query)))
        metrics = list(set(_METRIC_RE.findall(query)))
        is_compare = bool(_COMPARE_RE.search(query))
        is_trend = bool(_TREND_RE.search(query))
        time_range = _TIME_RANGE_RE.search(query)

        # Case 1: Multi-entity comparison
        if (is_compare or len(companies) > 1) and companies:
            return self._plan_comparison(query, companies, times, metrics)

        # Case 2: Time range / trend analysis
        if time_range or (is_trend and times):
            return self._plan_timeline(query, companies, times, metrics, time_range)

        # Case 3: Simple query → single search
        return ExecutionPlan(
            steps=[SearchStep(query=query, label="检索")],
            merge_type="union",
        )

    def _plan_comparison(self, query: str, companies: list[str],
                         times: list[str], metrics: list[str]) -> ExecutionPlan:
        """Plan for multi-entity comparison."""
        steps = []
        metric_str = " ".join(metrics[:3]) if metrics else "营收 利润"
        time_str = times[0] if times else ""

        for company in companies[:4]:  # Max 4 entities
            sub_query = f"{company} {time_str} {metric_str}".strip()
            steps.append(SearchStep(
                query=sub_query,
                label=f"搜索 {company}",
                entity=company,
                time_period=time_str,
                merge_key=company,
            ))

        plan = ExecutionPlan(
            steps=steps,
            merge_type="comparison",
            output_hint=f"请将{'/'.join(companies)}的数据整理成对比表格，包含{metric_str}。",
        )
        logger.info(f"Comparison plan: {len(steps)} entities × "
                    f"{len(metrics)} metrics → {len(steps)} searches")
        return plan

    def _plan_timeline(self, query: str, companies: list[str],
                       times: list[str], metrics: list[str],
                       time_range: re.Match | None) -> ExecutionPlan:
        """Plan for time-series analysis."""
        steps = []
        entity = companies[0] if companies else _leading_entity(query)
        metric_str = " ".join(metrics[:3]) if metrics else ""

        if time_range:
            start_year = int(time_range.group(1))
            end_year = int(time_range.group(2))
            years = list(range(start_year, end_year + 1))
        elif times:
            years = [t for t in times if re.match(r'20\d{2}', t)]
        else:
            years = ["2023", "2024"]

        for year in years[:5]:  # Max 5 years
            y = str(year).replace("年", "").strip()  # avoid '2024年年'
            sub_query = f"{entity} {y}年 {metric_str}".strip()
            steps.append(SearchStep(
                query=sub_query,
                label=f"{year}年",
                entity=entity,
                time_period=str(year),
                merge_key=str(year),
            ))

        plan = ExecutionPlan(
            steps=steps,
            merge_type="timeline",
            output_hint=f"请按时间顺序整理{entity}的{metric_str}数据，分析变化趋势。",
        )
        logger.info(f"Timeline plan: {len(steps)} years for {entity}")
        return plan


def execute_plan(plan: ExecutionPlan, pipeline, *, filters: dict | None = None) -> list[dict]:
    """Execute a retrieval plan and merge results.

    Args:
        plan: The execution plan
        pipeline: RetrievalPipeline instance

    Returns:
        Merged search results with labels
    """
    all_results = []
    seen_texts = set()

    for step in plan.steps:
        if filters:
            response = pipeline.search(step.query, top_k=3, filters=filters)
        else:
            response = pipeline.search(step.query, top_k=3)
        for r in response.results:
            text_key = r.text[:100]
            if text_key in seen_texts:
                continue
            seen_texts.add(text_key)
            all_results.append({
                "text": r.text,
                "filename": r.filename,
                "page": r.page,
                "section": r.section,
                "score": r.score,
                "step_label": step.label,
                "entity": step.entity,
                "merge_key": step.merge_key,
            })

    # Sort by score descending
    all_results.sort(key=lambda x: -x["score"])

    logger.info(f"Plan executed: {len(plan.steps)} steps → "
                f"{len(all_results)} unique results")
    return all_results

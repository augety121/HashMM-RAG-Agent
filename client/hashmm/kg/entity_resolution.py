"""v17 Phase 94 — entity resolution / canonicalization (Chinese-first).

Fixes the two visible KG-quality problems from the live build:
  * **fragmentation** — the same entity appears as繁/简 or alias variants
    ("小米集團" vs "小米集团", "网易云" vs "网易云音乐"), so edges never accumulate
    and average degree stays ~1.7;
  * **anaphoric/generic noise** — "本集团" (29 deg), "本公司董事", "2024年" leak in.

Design (matches the project conventions):
  * a **post-build pass** over a ``KnowledgeGraph`` — does NOT touch the live
    extraction path, so it can also clean the *already-saved* 1676-node graph in
    seconds (no 82-min rebuild);
  * **default OFF** (env ``HASHMM_KG_RESOLVE``) → zero behaviour change;
  * **conservative** — merges only within the same entity type, only on繁简-exact
    or substring+high-fuzzy matches → no over-merging ("小米集团"≠"小米金融");
  * **no fact loss by default** — merging unions edges; noise *dropping* is a
    separate opt-in (``HASHMM_KG_RESOLVE_DROP_NOISE``);
  * **zero new deps** — opencc / pypinyin / rapidfuzz are already installed; every
    import is defensive, so a missing lib just disables that one strategy (never raises).
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.entity_resolution")

# Types where fuzzy/substring merge is safe-ish. CONCEPT is intentionally excluded
# from fuzzy merge (too many, too easy to over-merge); 繁简-exact still applies to all.
_FUZZY_TYPES = {"ORG", "PERSON", "PRODUCT", "LOCATION"}


# ── env switches ─────────────────────────────────────────────────────────────
def resolve_enabled() -> bool:
    return os.environ.get("HASHMM_KG_RESOLVE", "").strip().lower() in {"1", "true", "yes", "on"}


def _drop_noise_enabled() -> bool:
    return os.environ.get("HASHMM_KG_RESOLVE_DROP_NOISE", "").strip().lower() in {"1", "true", "yes", "on"}


def _pinyin_enabled() -> bool:
    return os.environ.get("HASHMM_KG_RESOLVE_PINYIN", "").strip().lower() in {"1", "true", "yes", "on"}


def _fuzzy_threshold() -> int:
    try:
        return max(50, min(100, int(os.environ.get("HASHMM_KG_RESOLVE_FUZZY", "90"))))
    except ValueError:
        return 90


# ── normalization helpers (all defensive) ───────────────────────────────────
_OPENCC = None
_OPENCC_TRIED = False


def _simplified(name: str) -> str:
    """Traditional→Simplified + trim. Falls back to trimmed name if opencc absent."""
    global _OPENCC, _OPENCC_TRIED
    s = (name or "").strip()
    if not s:
        return s
    if not _OPENCC_TRIED:
        _OPENCC_TRIED = True
        try:
            import opencc  # type: ignore
            _OPENCC = opencc.OpenCC("t2s")
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            _OPENCC = None
    if _OPENCC is not None:
        try:
            return _OPENCC.convert(s).strip()
        except Exception:
            return s
    return s


def _pinyin(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin  # type: ignore
        return "".join(lazy_pinyin(name or ""))
    except Exception:
        return ""


def _fuzz(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz  # type: ignore
        return float(fuzz.ratio(a, b))
    except Exception:
        # crude fallback: exact → 100, substring → 85, else 0
        if a == b:
            return 100.0
        if a and b and (a in b or b in a):
            return 85.0
        return 0.0


# ── node merge (manual, to keep saved JSON clean) ────────────────────────────
def _degree(g, key) -> int:
    try:
        return g.degree(key)
    except Exception:
        return 0


def _merge_node(g, survivor: str, loser: str) -> None:
    """Redirect loser's edges onto survivor, union attrs, then drop loser."""
    if survivor == loser or not g.has_node(survivor) or not g.has_node(loser):
        return
    sdata, ldata = g.nodes[survivor], g.nodes[loser]
    # union source_ids + aliases (and record loser's surface name as an alias)
    sdata["source_ids"] = list(set(sdata.get("source_ids", []) + ldata.get("source_ids", [])))
    aliases = set(sdata.get("aliases", []) + ldata.get("aliases", []))
    lname = str(ldata.get("name", "")).strip()
    if lname and lname != str(sdata.get("name", "")):
        aliases.add(lname)
    sdata["aliases"] = sorted(aliases)
    # keep the longer/more specific display name
    if len(lname) > len(str(sdata.get("name", ""))):
        sdata["name"] = lname
    if len(str(ldata.get("description", ""))) > len(str(sdata.get("description", ""))):
        sdata["description"] = ldata.get("description", "")
    # redirect edges (DiGraph collapses parallel edges automatically)
    for p in list(g.predecessors(loser)):
        if p != survivor:
            g.add_edge(p, survivor, **g.get_edge_data(p, loser, default={}))
    for s in list(g.successors(loser)):
        if s != survivor:
            g.add_edge(survivor, s, **g.get_edge_data(loser, s, default={}))
    g.remove_node(loser)


# ── main pass ────────────────────────────────────────────────────────────────
def resolve_entities(kg: Any, fuzzy_threshold: int | None = None,
                     drop_noise: bool = False, use_pinyin: bool = False) -> dict:
    """Merge variant entities + (optionally) drop noise. In place. Never raises."""
    stats = {"before": 0, "after": 0, "merged_simplified": 0,
             "merged_fuzzy": 0, "dropped_noise": 0}
    try:
        g = kg.graph
        stats["before"] = g.number_of_nodes()
        thr = fuzzy_threshold if fuzzy_threshold is not None else _fuzzy_threshold()

        # group node keys by type
        by_type: dict[str, list] = defaultdict(list)
        for key, data in list(g.nodes(data=True)):
            by_type[str(data.get("type", "?"))].append(key)

        for typ, keys in by_type.items():
            # 1) 繁简-exact clusters (safe for ALL types)
            clusters: dict[str, list] = defaultdict(list)
            for k in keys:
                nm = str(g.nodes[k].get("name", k))
                clusters[_simplified(nm)].append(k)
            for _canon, members in clusters.items():
                if len(members) < 2:
                    continue
                survivor = max(members, key=lambda k: (_degree(g, k), len(str(g.nodes[k].get("name", k)))))
                for m in members:
                    if m != survivor and g.has_node(m):
                        _merge_node(g, survivor, m)
                        stats["merged_simplified"] += 1

            # 2) substring + high-fuzzy (only safe-ish types)
            if typ in _FUZZY_TYPES:
                alive = [k for k in dict.fromkeys(keys) if g.has_node(k)]
                i = 0
                while i < len(alive):
                    a = alive[i]
                    if not g.has_node(a):
                        i += 1; continue
                    na = _simplified(str(g.nodes[a].get("name", a)))
                    j = i + 1
                    while j < len(alive):
                        b = alive[j]
                        if not g.has_node(b):
                            j += 1; continue
                        nb = _simplified(str(g.nodes[b].get("name", b)))
                        shorter = min(len(na), len(nb)) if (na and nb) else 0
                        # strong signal: one is a substring of the other and the
                        # shorter name is ≥3 chars (avoids merging 小米⊂小米金融).
                        substr = bool(na and nb and na != nb and (na in nb or nb in na) and shorter >= 3)
                        # typo/near-duplicate: high fuzz AND near-equal length (no substring).
                        typo = bool(not substr and na and nb and na != nb
                                    and abs(len(na) - len(nb)) <= 2 and _fuzz(na, nb) >= thr)
                        pin = bool(use_pinyin and na and nb and na != nb and _pinyin(na) == _pinyin(nb))
                        if substr or typo or pin:
                            surv, lose = (a, b) if (_degree(g, a), len(na)) >= (_degree(g, b), len(nb)) else (b, a)
                            _merge_node(g, surv, lose)
                            stats["merged_fuzzy"] += 1
                            if lose == a:
                                a = surv; na = _simplified(str(g.nodes[a].get("name", a)))
                        j += 1
                    i += 1

        # 3) optional noise drop (opt-in — loses those edges)
        if drop_noise:
            try:
                from hashmm.kg.kg_metrics import classify_noise
                for key in list(g.nodes()):
                    nm = str(g.nodes[key].get("name", key))
                    if classify_noise(nm):
                        g.remove_node(key)
                        stats["dropped_noise"] += 1
            except Exception as e:  # noqa: BLE001
                log_suppressed(logger, e)

        stats["after"] = g.number_of_nodes()
        logger.info(
            "Entity resolution: %d → %d (繁简合并 %d, 模糊合并 %d, 丢弃噪声 %d)",
            stats["before"], stats["after"], stats["merged_simplified"],
            stats["merged_fuzzy"], stats["dropped_noise"],
        )
    except Exception as e:  # never raise from a cleanup pass
        log_suppressed(logger, e)
        stats["error"] = str(e)
    return stats


def apply_to_saved(drop_noise: bool | None = None) -> dict:
    """Load the saved KG, snapshot-then-resolve-then-save. For cleaning the
    existing graph without a full rebuild. Never raises."""
    try:
        from hashmm.kg.storage import KGStorage
        st = KGStorage()
        kg, comm = st.load()
        before = kg.num_entities
        stats = resolve_entities(
            kg,
            drop_noise=_drop_noise_enabled() if drop_noise is None else drop_noise,
            use_pinyin=_pinyin_enabled(),
        )
        st.save(kg, comm)
        stats["saved"] = True
        stats["entities_before"] = before
        stats["entities_after"] = kg.num_entities
        return stats
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"saved": False, "error": str(e)}


if __name__ == "__main__":  # CLI: clean the currently-saved KG in place
    print("实体消解（对已保存的 KG，原地清洗）...")
    print(apply_to_saved())

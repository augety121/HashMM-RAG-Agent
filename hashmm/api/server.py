"""HashMM-RAG v0.9 — Re-ranking + Semantic Cache + Persistence + Metrics.

New in v0.9:
  1. Re-ranking: hash粗排 → BGE-M3 cosine精排 (两阶段检索)
  2. Semantic Cache: 相似问题命中缓存，省 LLM 调用
  3. Persistent Storage: sessions/profiles/episodes 写磁盘，重启不丢
  4. Structured Metrics: 延迟/缓存命中率/token用量/检索命中率
"""
from __future__ import annotations
import asyncio, json, time, uuid, os, re, math, threading
from collections import defaultdict, Counter
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import numpy as np

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
SCORE_THRESHOLD = 0.72
RERANK_TOP = 5           # after hash top-K, rerank and keep top-5
CACHE_THRESHOLD = 0.92   # cosine similarity to consider cache hit
CACHE_MAX = 200          # max cached entries
PERSIST_DIR = Path("data/agent_state")
PERSIST_INTERVAL = 60    # auto-save every 60s

# ═══════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════
class Metrics:
    def __init__(self):
        self.total_queries = 0
        self.cache_hits = 0
        self.avg_latency_ms = 0
        self.total_llm_calls = 0
        self.total_retrieve_calls = 0
        self.intent_counts = Counter()
        self.skill_counts = Counter()
        self._latencies = []

    def record(self, latency_ms, intent, skills, cache_hit=False):
        self.total_queries += 1
        self._latencies.append(latency_ms)
        if len(self._latencies) > 500: self._latencies = self._latencies[-200:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        self.intent_counts[intent] += 1
        for s in skills: self.skill_counts[s] += 1
        if cache_hit: self.cache_hits += 1

    def to_dict(self):
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": f"{self.cache_hits/max(self.total_queries,1)*100:.1f}%",
            "avg_latency_ms": round(self.avg_latency_ms),
            "p95_latency_ms": round(sorted(self._latencies)[int(len(self._latencies)*0.95)] if self._latencies else 0),
            "total_llm_calls": self.total_llm_calls,
            "intents": dict(self.intent_counts.most_common(10)),
            "skills": dict(self.skill_counts.most_common(10)),
        }

metrics = Metrics()

# ═══════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════
_state: dict = {}
_llm_fn = None

def _load():
    global _llm_fn
    if _state.get("loaded"): return
    import torch
    from hashmm.config import HashMMConfig
    from hashmm.hashing.encoders import TextEncoder
    from hashmm.hashing.train import load_hash_net
    from hashmm.hashing.hash_net import pack_bits
    cfg = HashMMConfig()
    meta = []
    with open(Path(cfg.hash_index_dir) / "metadata.jsonl") as f:
        for line in f: meta.append(json.loads(line))
    text_enc = TextEncoder(model_name=cfg.hash_text_encoder, device=cfg.hash_device)
    hash_net, ckpt_meta = load_hash_net(cfg)
    import faiss
    fi = faiss.read_index_binary(str(cfg.hash_index_path))
    kw_index = _build_keyword_index(meta)
    _state.update(loaded=True, text_enc=text_enc, hash_net=hash_net,
                  faiss_index=fi, metadata=meta, bits=ckpt_meta["bits"],
                  cfg=cfg, pack_bits=pack_bits, kw_index=kw_index)
    from hashmm.agent.llm import make_llm_fn
    _llm_fn = make_llm_fn(cfg)
    print(f"[Agent] {fi.ntotal} chunks, {ckpt_meta['bits']}-bit. "
          f"LLM: {'ready' if _llm_fn else 'N/A'}", flush=True)

def _llm(prompt: str) -> str:
    if not _llm_fn: raise RuntimeError("LLM not configured")
    metrics.total_llm_calls += 1
    return _llm_fn(prompt)


# ═══════════════════════════════════════════════════════════════════
# SEMANTIC CACHE (embedding-based)
# ═══════════════════════════════════════════════════════════════════
class SemanticCache:
    """Cache LLM answers by query embedding similarity."""
    def __init__(self, threshold=CACHE_THRESHOLD, max_size=CACHE_MAX):
        self.threshold = threshold
        self.max_size = max_size
        self.entries: list[dict] = []  # {emb, query, answer, sources, ts}

    def lookup(self, q_emb: np.ndarray) -> dict | None:
        if not self.entries: return None
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        best_sim, best_entry = 0, None
        for e in self.entries:
            sim = float(np.dot(q_norm.flatten(), e["emb"].flatten()))
            if sim > best_sim:
                best_sim, best_entry = sim, e
        if best_sim >= self.threshold:
            return best_entry
        return None

    def store(self, q_emb: np.ndarray, query: str, answer: str, sources: list):
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        self.entries.append({"emb": q_norm.flatten(), "query": query,
                              "answer": answer, "sources": sources, "ts": time.time()})
        if len(self.entries) > self.max_size:
            self.entries = self.entries[-self.max_size // 2:]  # evict oldest half

sem_cache = SemanticCache()


# ═══════════════════════════════════════════════════════════════════
# PERSISTENT STORAGE
# ═══════════════════════════════════════════════════════════════════
class PersistentMemory:
    def __init__(self, persist_dir=PERSIST_DIR):
        self.dir = Path(persist_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, dict] = {}
        self.history: dict[str, list] = defaultdict(list)
        self.profiles: dict[str, dict] = {}
        self.episodes: list[dict] = []
        self._load_from_disk()
        self._start_auto_save()

    def _load_from_disk(self):
        for name in ["sessions", "profiles"]:
            p = self.dir / f"{name}.json"
            if p.exists():
                try:
                    setattr(self, name, json.loads(p.read_text()))
                    print(f"[Persist] Loaded {name}: {len(getattr(self, name))} entries")
                except: pass
        p = self.dir / "episodes.json"
        if p.exists():
            try:
                self.episodes = json.loads(p.read_text())
                print(f"[Persist] Loaded episodes: {len(self.episodes)}")
            except: pass
        p = self.dir / "history.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.history = defaultdict(list, data)
                print(f"[Persist] Loaded history: {len(self.history)} sessions")
            except: pass

    def save_to_disk(self):
        try:
            for name in ["sessions", "profiles"]:
                (self.dir / f"{name}.json").write_text(
                    json.dumps(getattr(self, name), ensure_ascii=False, default=str))
            (self.dir / "episodes.json").write_text(
                json.dumps(self.episodes[-500:], ensure_ascii=False, default=str))
            (self.dir / "history.json").write_text(
                json.dumps(dict(self.history), ensure_ascii=False, default=str))
        except Exception as e:
            print(f"[Persist] Save failed: {e}", flush=True)

    def _start_auto_save(self):
        def _auto():
            while True:
                time.sleep(PERSIST_INTERVAL)
                self.save_to_disk()
        t = threading.Thread(target=_auto, daemon=True)
        t.start()

    def get_or_create_session(self, sid, title=""):
        if sid not in self.sessions:
            self.sessions[sid] = {"title": title[:30], "created": time.time()}
        return self.sessions[sid]

    def add_message(self, sid, role, content):
        self.history[sid].append({"role": role, "content": content, "ts": time.time()})

    def get_history(self, sid): return self.history[sid]

    def update_profile(self, sid, query, intent, topics):
        if sid not in self.profiles:
            self.profiles[sid] = {"topics": {}, "intents": {}, "queries": 0, "lang_pref": {}}
        p = self.profiles[sid]
        p["queries"] += 1
        p["intents"][intent] = p["intents"].get(intent, 0) + 1
        for t in topics: p["topics"][t] = p["topics"].get(t, 0) + 1
        lang = "zh" if re.search(r'[\u4e00-\u9fff]', query) else "en"
        p["lang_pref"][lang] = p["lang_pref"].get(lang, 0) + 1

    def get_profile_context(self, sid):
        p = self.profiles.get(sid)
        if not p or p["queries"] < 3: return ""
        top = sorted(p["topics"].items(), key=lambda x: -x[1])[:5]
        if not top: return ""
        return f"[用户画像] 关注：{'、'.join(t for t,_ in top)}。\n"

    def log_episode(self, **kwargs):
        self.episodes.append({"ts": time.time(), **kwargs})
        if len(self.episodes) > 2000:
            self.episodes = self.episodes[-1000:]

memory = PersistentMemory()


# ═══════════════════════════════════════════════════════════════════
# SAFETY (same as v0.8)
# ═══════════════════════════════════════════════════════════════════
BANNED_PATTERNS = [
    r'ignore\s+(previous|above|all)\s+(instructions?|prompts?)',
    r'system\s*prompt', r'你的指令', r'忽略.*指令', r'jailbreak', r'DAN\s+mode',
    r'如何制[造作].*[炸弹武器毒]', r'how\s+to\s+(make|build)\s+(bomb|weapon)',
]
BANNED_WORDS = {'色情', '赌博', '毒品', '自杀方法', '恐怖袭击'}

def check_safety(query):
    q = query.lower().strip()
    if len(q) < 1: return False, "输入为空"
    if len(query) > 2000: return False, "输入过长"
    for w in BANNED_WORDS:
        if w in query: return False, "检测到违禁内容，无法处理"
    for p in BANNED_PATTERNS:
        if re.search(p, q, re.IGNORECASE): return False, "检测到不安全输入，请重新表述"
    if re.search(r'<script|javascript:|on\w+=', q): return False, "检测到不安全内容"
    return True, ""

def sanitize_output(text):
    return re.sub(r'(?i)(system\s*prompt|我的指令|我被要求)', '[已过滤]', text)


# ═══════════════════════════════════════════════════════════════════
# KEYWORD INDEX + RETRIEVAL SKILLS
# ═══════════════════════════════════════════════════════════════════
def _tokenize(t): return re.findall(r'[a-zA-Z]{2,}', t.lower())

def _build_keyword_index(meta):
    idf = Counter(); dt = []; al = 0
    for e in meta:
        tk = _tokenize(e.get("text","")); dt.append(tk); idf.update(set(tk)); al += len(tk)
    n = len(meta); al /= max(n,1)
    return {"idf": {t: math.log((n-df+.5)/(df+.5)+1) for t,df in idf.items()}, "dt": dt, "al": al}

def skill_hash_search(query, top_k=20):
    """Stage 1: hash粗排 — fast Hamming distance."""
    import torch
    s = _state; t0 = time.perf_counter()
    with torch.no_grad():
        q_emb = s["text_enc"]([query]).to(s["cfg"].hash_device)
        q_code = s["hash_net"].sign_text(q_emb)
        q_packed = s["pack_bits"](q_code).cpu().numpy().astype(np.uint8)
    dists, idxs = s["faiss_index"].search(q_packed, min(top_k * 5, 200))
    ms = (time.perf_counter() - t0) * 1000
    metrics.total_retrieve_calls += 1
    results = []
    for d, i in zip(dists[0], idxs[0]):
        if i < 0 or i >= len(s["metadata"]): continue
        e = s["metadata"][i]
        text = (e.get("text") or "").strip()
        if e.get("modality") in ("equation","chart"): continue
        if len(text) < 100: continue
        results.append({"idx": i, "rank": 0, "chunk_id": e.get("chunk_id",""),
                         "doc_id": e.get("doc_id",""), "modality": e.get("modality",""),
                         "text": text[:600], "score": int(s["bits"]-d), "method": "hash"})
    return results[:top_k], ms, q_emb

def skill_rerank(results, q_emb, top_k=RERANK_TOP):
    """Stage 2: BGE-M3 cosine精排 — re-score hash candidates."""
    import torch
    if not results: return [], 0
    s = _state; t0 = time.perf_counter()
    # Encode candidate texts
    texts = [r["text"] for r in results]
    with torch.no_grad():
        doc_embs = s["text_enc"](texts).cpu().numpy()
    q_np = q_emb.cpu().numpy().astype(np.float32)
    # Cosine similarity
    q_norm = q_np / (np.linalg.norm(q_np) + 1e-9)
    d_norm = doc_embs / (np.linalg.norm(doc_embs, axis=1, keepdims=True) + 1e-9)
    sims = (d_norm @ q_norm.T).flatten()
    # Sort by cosine
    ranked = sorted(zip(results, sims), key=lambda x: -x[1])
    ms = (time.perf_counter() - t0) * 1000
    reranked = []
    for rank, (r, sim) in enumerate(ranked[:top_k]):
        r["score"] = round(float(sim), 4)
        r["rank"] = rank + 1
        r["method"] = "hash→rerank"
        reranked.append(r)
    return reranked, ms

def skill_keyword_search(query, top_k=10):
    t0 = time.perf_counter(); s = _state; idx = s["kw_index"]
    qt = _tokenize(query)
    if not qt: return [], 0
    scores = []
    for di, dt in enumerate(idx["dt"]):
        if not dt: continue
        tf = Counter(dt); dl = len(dt); sc = 0
        for q in qt:
            if q not in idx["idf"]: continue
            f = tf.get(q,0)
            if f==0: continue
            sc += idx["idf"][q] * (f*2.5) / (f + 1.5*(1-.75+.75*dl/idx["al"]))
        if sc > 0: scores.append((di, sc))
    scores.sort(key=lambda x: -x[1])
    ms = (time.perf_counter() - t0) * 1000
    results = []
    for rank, (di, sc) in enumerate(scores[:top_k]):
        e = s["metadata"][di]; text = (e.get("text") or "").strip()
        if len(text) < 100: continue
        results.append({"rank": rank+1, "chunk_id": e.get("chunk_id",""),
                         "doc_id": e.get("doc_id",""), "modality": e.get("modality",""),
                         "text": text[:600], "score": round(sc,2), "method": "keyword"})
    return results[:top_k], ms

def hook_dedup(results):
    seen=[]; out=[]
    for r in results:
        t=r["text"][:200].lower(); dup=False
        for s in seen:
            if len(set(t.split())&set(s.split()))>10: dup=True; break
        if not dup: out.append(r); seen.append(t)
    return out

def hook_relevance(query, results):
    if not results: return False
    bits = _state.get("bits", 256)
    hr = [r for r in results if "hash" in r.get("method","")]
    if hr and max(r["score"] for r in hr) < bits * SCORE_THRESHOLD: return False
    qw = set(_tokenize(query))
    for r in results[:3]:
        if len(qw & set(_tokenize(r["text"]))) >= 2: return True
    return bool(results) and not hr

def hook_rewrite(query):
    if not re.search(r'[\u4e00-\u9fff]', query) or not _llm_fn: return None
    try:
        kw = _llm("将以下中文问题转为英文学术检索关键词（只输出关键词）：\n"
                   f"{query}\n关键词：").strip().split('\n')[0][:100]
        if kw and len(kw) > 3: return kw
    except: pass
    return None


# ═══════════════════════════════════════════════════════════════════
# INTENT + FOLLOW-UP + GENERATION
# ═══════════════════════════════════════════════════════════════════
FOLLOWUP_RE = [r'^(它|这个|那个|上面的|刚才的|详细|具体|展开|继续|更多|解释一下)']

def detect_followup(query, history):
    if not history: return None
    for p in FOLLOWUP_RE:
        if re.search(p, query) and _llm_fn:
            last_u = next((h["content"] for h in reversed(history) if h["role"]=="user"),"")
            last_a = next((h["content"][:150] for h in reversed(history) if h["role"]=="assistant"),"")
            if last_u:
                try:
                    exp = _llm(f"之前问：{last_u}\n助手答（摘要）：{last_a[:120]}\n"
                               f"现在说：{query}\n扩展为完整问题（一句话）：").strip().split('\n')[0][:200]
                    if len(exp) > len(query): return exp
                except: pass
    return None

def classify_intent(query):
    if not _llm_fn: return "academic_kb"
    try:
        r = _llm("Classify (name only): academic_kb / academic_open / compare / chitchat\n"
                  f"Query: {query}\nCategory:").strip().lower()
        for c in ["academic_kb","academic_open","compare","chitchat"]:
            if c in r: return c
    except: pass
    return "academic_kb"

def extract_topics(query):
    topics = []
    for p in [r'\b(DCMH|DJSRH|ColPali|ColBERT|Hash-RAG|DPSH|HashNet)\b',
              r'\b(cross-modal|hashing|retrieval|quantization|RAG|embedding)\b',
              r'(跨模态|哈希|检索|量化|向量|编码)']:
        topics.extend(re.findall(p, query, re.IGNORECASE))
    return list(set(t.lower() if isinstance(t,str) else t for t in topics))

SYS = {
    "kb": "你是学术助手。基于检索片段回答，用[1][2]引用。不用#标题。公式用$$或$。\n",
    "compare": "你是学术助手。对比分析，用完整markdown表格（每行每列填满，不要省略）。不用#标题。公式用$$或$。\n",
    "open": "你是学术助手。用知识直接回答，不引用检索。不用#标题。公式用$$或$。有深度。\n",
    "chat": "你是 HashMM-RAG 学术助手。友好简短回应。\n",
}

def generate(query, results, history, sys_key, profile_ctx=""):
    sys = SYS.get(sys_key, SYS["kb"])
    ctx = "\n\n".join(f"[来源{i}] ({r['modality']}) {r['text']}"
                       for i, r in enumerate(results[:5], 1) if r["text"].strip())
    hist = "".join(f"{'用户' if h['role']=='user' else '助手'}：{h['content'][:200]}\n"
                    for h in history[-6:])
    return _llm(f"{sys}\n{profile_ctx}{'对话历史：\n'+hist if hist else ''}"
                f"{'检索结果：\n'+ctx+chr(10)*2 if ctx else ''}用户问题：{query}\n\n回答：")

def evaluate_answer(query, answer):
    if not _llm_fn: return 5
    try:
        r = _llm(f"Rate 1-5 (number only):\nQ: {query}\nA: {answer[:300]}\nScore:")
        return int(re.search(r'[1-5]', r.strip()).group())
    except: return 4


# ═══════════════════════════════════════════════════════════════════
# AGENT PIPELINE
# ═══════════════════════════════════════════════════════════════════

def agent_run(query: str, session_id: str) -> dict:
    _load()
    history = memory.get_history(session_id)
    trace = []; sources = []; t0 = time.time(); quality = 0; skills_used = []

    # Safety
    safe, reason = check_safety(query)
    if not safe:
        trace.append({"node": "safety", "detail": f"⚠️ {reason}"})
        return {"answer": f"⚠️ {reason}", "sources": [], "trace": trace,
                "elapsed_ms": 0, "intent": "blocked", "rewritten_query": None}

    query = " ".join(query.split())[:2000]

    # Follow-up
    expanded = detect_followup(query, history)
    if expanded:
        trace.append({"node": "understand", "detail": f"展开：{expanded[:60]}"})
        query = expanded

    # Profile
    topics = extract_topics(query)
    intent = classify_intent(query)
    memory.update_profile(session_id, query, intent, topics)
    profile_ctx = memory.get_profile_context(session_id)
    trace.append({"node": "classify", "detail": f"{intent} · {','.join(topics[:3]) or '—'}"})

    # Rewrite
    rewritten = query
    en_q = hook_rewrite(query)
    if en_q: rewritten = en_q; trace.append({"node": "rewrite", "detail": f"→ {en_q}"})

    # ── Semantic Cache Check ──
    q_emb = None
    try:
        import torch
        with torch.no_grad():
            q_emb = _state["text_enc"]([rewritten]).cpu().numpy()
        cached = sem_cache.lookup(q_emb)
        if cached and intent not in ("chitchat",):
            trace.append({"node": "cache", "detail": f"命中缓存（原问：{cached['query'][:40]}）"})
            elapsed = (time.time() - t0) * 1000
            trace.append({"node": "done", "detail": f"{elapsed:.0f}ms (cached)"})
            metrics.record(elapsed, intent, ["cache"], cache_hit=True)
            return {"answer": cached["answer"], "sources": cached["sources"],
                    "trace": trace, "elapsed_ms": round(elapsed,2),
                    "intent": intent, "rewritten_query": rewritten if rewritten != query else None}
    except: pass

    # ── Skill Execution ──
    try:
        if intent == "chitchat":
            answer = _llm(f"{SYS['chat']}\n{profile_ctx}问题：{query}\n回答：")
            skills_used.append("llm_direct")

        elif intent == "academic_open":
            answer = generate(query, [], history, "open", profile_ctx)
            skills_used.append("llm_direct")

        elif intent in ("academic_kb", "compare"):
            # Stage 1: Hash粗排
            results, ms_hash, hash_q_emb = skill_hash_search(rewritten, 20)
            results = hook_dedup(results)
            skills_used.append("hash")
            trace.append({"node": "skill:hash", "detail": f"粗排 {len(results)} 条 / {ms_hash:.0f}ms"})

            relevant = hook_relevance(rewritten, results)

            if relevant and results:
                # Stage 2: BGE-M3 cosine精排
                reranked, ms_rerank = skill_rerank(results, hash_q_emb, RERANK_TOP)
                skills_used.append("rerank")
                trace.append({"node": "skill:rerank", "detail": f"精排 top-{len(reranked)} / {ms_rerank:.0f}ms"})
                sources = reranked
            elif not relevant:
                # Fallback: keyword
                kw_r, kw_ms = skill_keyword_search(rewritten, 10)
                kw_r = hook_dedup(kw_r)
                skills_used.append("keyword")
                trace.append({"node": "skill:keyword", "detail": f"{len(kw_r)} 条 / {kw_ms:.0f}ms"})
                if kw_r: sources = kw_r[:5]

            if sources:
                sys_key = "compare" if intent == "compare" else "kb"
                answer = generate(query, sources, history, sys_key, profile_ctx)
                trace.append({"node": "generate", "detail": f"基于 {len(sources)} 条"})
            else:
                answer = generate(query, [], history, "open", profile_ctx)
                skills_used.append("llm_direct")
                trace.append({"node": "generate", "detail": "LLM 直接回答"})

            quality = evaluate_answer(query, answer)
            trace.append({"node": "evaluate", "detail": f"质量: {quality}/5"})
        else:
            answer = generate(query, [], history, "open", profile_ctx)

    except Exception as e:
        answer = f"处理出错：{e}"
        trace.append({"node": "error", "detail": str(e)})

    answer = sanitize_output(answer)
    elapsed = (time.time() - t0) * 1000
    trace.append({"node": "done", "detail": f"{elapsed:.0f}ms"})

    # Cache result
    if q_emb is not None and intent not in ("chitchat",) and len(answer) > 50:
        sem_cache.store(q_emb, query, answer, sources)

    # Log
    memory.log_episode(query=query[:100], intent=intent, skills=skills_used,
                        n_sources=len(sources), elapsed_ms=round(elapsed), quality=quality)
    metrics.record(elapsed, intent, skills_used)

    return {"answer": answer, "sources": sources, "trace": trace,
            "elapsed_ms": round(elapsed, 2), "intent": intent,
            "rewritten_query": rewritten if rewritten != query else None}


# ═══════════════════════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════════════════════
class ChatRequest(BaseModel):
    message: str; session_id: str | None = None

@asynccontextmanager
async def lifespan(app):
    if os.environ.get("EAGER_LOAD") == "1": _load()
    yield
    memory.save_to_disk()  # save on shutdown

app = FastAPI(title="HashMM-RAG", version="0.9.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_fe = next((p for p in [Path("frontend"), Path(__file__).parent.parent.parent / "frontend"]
            if (p / "index.html").exists()), None)

@app.get("/")
async def root():
    return FileResponse(_fe / "index.html") if _fe else {"msg": "HashMM-RAG v0.9.0"}

@app.get("/api/health")
async def health():
    return {"status": "ok", "loaded": _state.get("loaded", False), "llm": _llm_fn is not None,
            "version": "0.9.0"}

@app.get("/api/corpus/stats")
async def stats():
    _load(); s = _state
    mods = {}
    for e in s["metadata"]:
        m = e.get("modality", "?"); mods[m] = mods.get(m, 0) + 1
    p = s["cfg"].hash_index_path
    return {"total_chunks": s["faiss_index"].ntotal, "hash_bits": s["bits"],
            "index_size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0,
            "modalities": mods, "llm_ready": _llm_fn is not None,
            "cache_size": len(sem_cache.entries), "episodes": len(memory.episodes)}

@app.get("/api/metrics")
async def get_metrics():
    return metrics.to_dict()

@app.get("/api/sessions")
async def list_sessions():
    items = [{"id": k, **v} for k, v in memory.sessions.items()]
    items.sort(key=lambda x: x.get("created", 0), reverse=True)
    return items[:50]

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return {"messages": memory.history.get(session_id, []),
            "info": memory.sessions.get(session_id, {})}

@app.get("/api/experience")
async def get_experience():
    return {"total": len(memory.episodes), "recent": memory.episodes[-20:],
            "profiles": memory.profiles}

@app.post("/api/chat")
async def chat(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    memory.get_or_create_session(sid, req.message)
    memory.add_message(sid, "user", req.message)
    result = agent_run(req.message, sid)
    memory.add_message(sid, "assistant", result["answer"])
    return {"answer": result["answer"], "session_id": sid, "sources": result["sources"],
            "trace": result["trace"], "elapsed_ms": result["elapsed_ms"],
            "intent": result["intent"], "rewritten_query": result.get("rewritten_query")}

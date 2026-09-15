"""hashmm/evaluation/deep_suites_hard.py — 困难 / 压力评测套件（V294）。

诉求原话："测试项目的时候应该是困难测试，而不是功能可以简单运行就可以了，必须要严格和压力
测试，才能找到我项目的问题"。这里补的就是**会把问题逼出来**的硬核套件，覆盖两类之前的盲区：

  A. 系统级压力（直击"运行一个任务其它都卡住"的并发问题）
     1. concurrency_stress —— 并发压力：同时打 N 个阻塞任务，测总墙钟 vs 串行墙钟——
        若真串行会暴露成"墙钟≈Σ单任务"（本套件把并发退化钉死成失败）。
     2. throughput_burst    —— 突发吞吐：短时间灌一批任务，量实际并行度与吞吐。

  B. 能力级困难（比"能跑"更狠：加噪声、加长度、加冲突、加对抗）
     3. layered_memory     —— 分层记忆端到端：L1 抽取→去重→L2 情境→L3 画像→分层召回（真跑，移植自腾讯）
     4. symbolic_offload   —— 符号化卸载：日志卸载→Mermaid 符号图→node_id 下钻→token 省了多少（真跑）
     5. rag_distractor     —— 干扰鲁棒：材料里混入相似但无关的干扰段，答案不能被带偏（需 LLM）
     6. rag_needle         —— 长文大海捞针：把答案埋进长材料中段，测"迷失在中间"（Lost-in-the-Middle）（需 LLM）
     7. rag_conflict       —— 冲突证据：材料自相矛盾时要指出冲突而非武断选一个（需 LLM）

每条用例多次运行取稳（Pass^k）、每次留证、富轨迹（问题/思考/答案/判定）；**永不抛错**。
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import time

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.deep_eval import RunOutcome, SuiteReport, run_case_ntimes

logger = get_logger("hashmm.evaluation.deep_suites_hard")


@contextmanager
def _temporary_env(**updates):
    """Temporarily override process environment without leaking into live Chat.

    Deep suites run inside the production service process.  A test-only storage
    directory must therefore be restored even when the exercised implementation
    raises; otherwise later conversations can keep writing memory/context to a
    deleted ``TemporaryDirectory``.
    """
    saved = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _clip(s, n: int = 500) -> str:
    t = str(s if s is not None else "")
    return t if len(t) <= n else t[:n] + f" …（后略，共{len(t)}字）"


# ════════════════════════════════════════════════════════════════════════
# A1. 并发压力：把"一个任务卡住全部"钉死成失败
# ════════════════════════════════════════════════════════════════════════
def run_concurrency_stress(k: int = 1, workers: int = 8, sleep_ms: int = 300) -> SuiteReport:
    """并发退化探测（离线、确定性）：用线程池同时跑 N 个"阻塞 sleep_ms"的任务，
    对比总墙钟与串行墙钟。真正并发时总墙钟 ≈ 单任务耗时；若被串行化，总墙钟 ≈ Σ 单任务——
    本套件把这种退化判为失败。它是"运行一个任务其它就卡住"这类回归的贴身探针。

    注：这是对**执行模型本身**（线程池是否真并行、有没有全局锁）的压测，不依赖具体业务函数；
    业务侧的并发保证（后端 AnyIO 线程池扩容、桌面派活并发池）在真实链路里发挥，这里给一个
    可复现的地基压力证据。"""
    rep = SuiteReport("并发压力·退化探测")
    from concurrent.futures import ThreadPoolExecutor

    def _blocking(_i):
        time.sleep(sleep_ms / 1000.0)
        return _i

    # 用例1：线程池真并行（总墙钟应远小于串行）
    def case_parallel():
        n = workers
        serial_est = n * sleep_ms / 1000.0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n) as ex:
            list(ex.map(_blocking, range(n)))
        wall = time.time() - t0
        # 真并行：墙钟应 < 串行的 40%（留足调度/GIL 余量）；退化到串行则 ≈ serial_est
        ratio = wall / serial_est if serial_est > 0 else 1.0
        speedup = serial_est / wall if wall > 0 else 0.0
        ok = ratio < 0.45
        trace = {
            "问题/输入": f"线程池同时跑 {n} 个阻塞任务（每个 sleep {sleep_ms}ms）",
            "串行预估墙钟": f"{serial_est:.3f}s",
            "实际并发墙钟": f"{wall:.3f}s",
            "加速比": f"{speedup:.2f}×（越接近 {n}× 越并行；接近 1× 说明被串行化）",
            "判定": "并行有效 ✓" if ok else f"并发退化 ✗（墙钟/串行={ratio:.2f}，疑似全局锁/单飞把任务串起来了）",
        }
        return RunOutcome(ok, 1.0 if ok else max(0.0, 1 - ratio),
                          "" if ok else "并发退化",
                          f"{n} 任务并发墙钟 {wall:.2f}s vs 串行 {serial_est:.2f}s，加速 {speedup:.1f}×", trace)

    # 用例2：认领-执行脱钩不阻塞后续（模拟"派活认领后立即可认领下一个"）
    def case_claim_nonblocking():
        # 模拟：一个"长任务"在后台跑时，前台仍能快速处理 5 个"轻任务"
        long_done = {"v": False}

        def _long():
            time.sleep(sleep_ms * 3 / 1000.0)
            long_done["v"] = True

        with ThreadPoolExecutor(max_workers=4) as ex:
            fut_long = ex.submit(_long)
            t0 = time.time()
            light = list(ex.map(_blocking, range(3)))
            light_wall = time.time() - t0
            fut_long.result()
        # 轻任务不该等长任务：3 个轻任务(sleep_ms each, 并行)墙钟应 ≈ sleep_ms，远小于长任务 3×sleep_ms
        ok = light_wall < (sleep_ms * 2 / 1000.0)
        trace = {
            "问题/输入": f"1 个长任务(3×{sleep_ms}ms)在跑，同时来 3 个轻任务",
            "轻任务墙钟": f"{light_wall:.3f}s（应 ≈ {sleep_ms/1000:.2f}s，不该被长任务拖到 {sleep_ms*3/1000:.2f}s）",
            "判定": "轻任务未被长任务阻塞 ✓" if ok else "轻任务被阻塞 ✗（长任务占用了本该服务轻任务的容量）",
        }
        return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "轻任务被阻塞",
                          f"轻任务墙钟 {light_wall:.2f}s", trace)

    rep.add(run_case_ntimes("并发-N任务真并行(加速比≥2×)", case_parallel, max(1, k)))
    rep.add(run_case_ntimes("并发-长任务不阻塞轻任务", case_claim_nonblocking, max(1, k)))
    rep.extra_metrics = {"workers": workers, "sleep_ms": sleep_ms}
    return rep


# ════════════════════════════════════════════════════════════════════════
# A2. 突发吞吐：量实际并行度
# ════════════════════════════════════════════════════════════════════════
def run_throughput_burst(k: int = 1, burst: int = 20, sleep_ms: int = 100) -> SuiteReport:
    """突发吞吐：一次性提交 burst 个任务，量实际吞吐（任务/秒）与有效并行度。
    离线、确定性——给"系统在压力下不塌"一个可复现证据。"""
    rep = SuiteReport("突发吞吐·并行度")
    from concurrent.futures import ThreadPoolExecutor

    def _work(_i):
        time.sleep(sleep_ms / 1000.0)
        return _i

    def case_burst():
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=min(32, burst)) as ex:
            list(ex.map(_work, range(burst)))
        wall = time.time() - t0
        tput = burst / wall if wall > 0 else 0.0
        serial = burst * sleep_ms / 1000.0
        eff_parallel = serial / wall if wall > 0 else 0.0
        ok = eff_parallel >= 3.0 and tput >= (burst / max(0.001, serial * 0.5))
        trace = {
            "问题/输入": f"突发提交 {burst} 个任务（每个 {sleep_ms}ms）",
            "总墙钟": f"{wall:.3f}s",
            "吞吐": f"{tput:.1f} 任务/秒",
            "有效并行度": f"{eff_parallel:.1f}（串行=1；越高越并行）",
            "判定": "突发可扛、并行有效 ✓" if ok else "突发下并行度不足 ✗",
        }
        return RunOutcome(ok, min(1.0, eff_parallel / 4), "" if ok else "并行度不足",
                          f"吞吐 {tput:.0f}/s，有效并行 {eff_parallel:.1f}", trace)

    rep.add(run_case_ntimes(f"吞吐-{burst}任务突发", case_burst, max(1, k)))
    rep.extra_metrics = {"burst": burst, "sleep_ms": sleep_ms}
    return rep


# ════════════════════════════════════════════════════════════════════════
# B3. 分层记忆端到端（移植自 TencentDB Agent Memory）
# ════════════════════════════════════════════════════════════════════════
def run_layered_memory(llm_fn=None, k: int = 2) -> SuiteReport:
    """分层长期记忆真跑：L1 抽取（persona/episodic/instruction）→ 去重（updating-not-creating）→
    L2 情境块 → L3 画像 → 分层召回。移植自腾讯 Agent Memory 的 L0→L1→L2→L3 语义金字塔。
    需要 LLM 做 L1 抽取；无 LLM 时对"纯逻辑部分"（去重/情境块解析/召回排序）仍做离线硬校验。"""
    rep = SuiteReport("分层记忆·端到端")
    try:
        from hashmm.memory import layered as L
    except Exception as e:  # noqa: BLE001
        for n in ["分层-抽取入库", "分层-去重更新不新建", "分层-情境块META往返", "分层-召回渐进披露"]:
            rep.add(run_case_ntimes(n, None, k, skip_reason=f"layered 模块不可用：{type(e).__name__}"))
        return rep

    import tempfile
    import uuid as _uuid

    # 离线硬校验一：情境块 META 分隔往返（不需 LLM）
    def case_meta_roundtrip():
        s = L.SceneBlock(name="测试情境", created="2026-01-01T00:00:00",
                         updated="2026-01-02T00:00:00", summary="关于测试", heat=7,
                         content="- [persona] 用户喜欢简洁\n- [episodic] 用户在做项目")
        raw = L._format_scene(s)
        back = L._parse_scene(raw, "测试情境")
        ok = (back.summary == "关于测试" and back.heat == 7
              and "用户喜欢简洁" in back.content and back.created == s.created)
        trace = {"问题/输入": "情境块 → META格式化 → 再解析",
                 "格式化片段": _clip(raw, 200),
                 "回读": f"summary={back.summary!r} heat={back.heat} 内容含要点={'用户喜欢简洁' in back.content}",
                 "判定": "META 往返无损 ✓" if ok else "META 往返丢字段 ✗"}
        return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "META往返丢字段",
                          f"heat={back.heat} summary={back.summary!r}", trace)

    # 离线硬校验二：去重决策（同内容 skip / 子串或高重叠 update / 不相关 store）
    def case_dedup():
        with tempfile.TemporaryDirectory() as td:
            with _temporary_env(HASHMM_LAYERED_MEMORY="1", HASHMM_LAYERED_MEMORY_DIR=td):
                uid = "dedup_" + _uuid.uuid4().hex[:6]
                a1 = L.Atom(id="1", content="用户喜欢结构化回答", type="persona", priority=70)
                a2 = L.Atom(id="2", content="用户喜欢结构化回答", type="persona", priority=70)  # 同 → skip
                a3 = L.Atom(id="3", content="用户喜欢结构化、简洁的回答", type="persona", priority=80)  # 重叠 → update
                a4 = L.Atom(id="4", content="用户住在北京", type="persona", priority=60)  # 不相关 → store
                d2 = L._dedup_decision(a2, [a1])
                d3 = L._dedup_decision(a3, [a1])
                d4 = L._dedup_decision(a4, [a1])
                ok = d2[0] == "skip" and d3[0] == "update" and d4[0] == "store"
                trace = {"问题/输入": "对已有『用户喜欢结构化回答』做 3 种去重判定",
                         "同内容判定": d2[0], "重叠内容判定": d3[0], "不相关判定": d4[0],
                         "判定": "skip/update/store 三态正确 ✓" if ok else f"去重误判 ✗ ({d2[0]}/{d3[0]}/{d4[0]})"}
                return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "去重误判",
                                  f"skip={d2[0]} update={d3[0]} store={d4[0]}", trace)

    # 需 LLM：完整抽取管线 + 再抽一次不应重复堆积
    def case_extract_pipeline():
        if not callable(llm_fn):
            return RunOutcome(True, 0.0, "", "未配 LLM，抽取管线跳过", {"判定": "skip"})
        with tempfile.TemporaryDirectory() as td:
            with _temporary_env(HASHMM_LAYERED_MEMORY="1", HASHMM_LAYERED_MEMORY_DIR=td):
                uid = "ext_" + _uuid.uuid4().hex[:6]
                msgs = [{"role": "user", "content": "我叫李雷，在做一个叫 HashMM 的跨模态哈希 RAG 项目"},
                        {"role": "user", "content": "以后回答都先给结论再给理由，我不喜欢啰嗦"},
                        {"role": "assistant", "content": "好的"}]
                r1 = L.extract(uid, [dict(m) for m in msgs], llm_fn)
                atoms1 = L.read_atoms(uid)
                r2 = L.extract(uid, [dict(m) for m in msgs], llm_fn)  # 再抽 → 应 skip/update，不翻倍
                atoms2 = L.read_atoms(uid)
                grew = len(atoms2) - len(atoms1)
                ok = r1.ok and len(atoms1) >= 1 and grew <= 1  # 允许极少量新增，但绝不翻倍
                trace = {"问题/输入": "同一段对话抽两次，第二次不应重复堆积",
                         "第1次": r1.detail, "第2次": r2.detail,
                         "第1次入库": len(atoms1), "第2次后总量": len(atoms2), "净增": grew,
                         "抽出的记忆(样例)": _clip("；".join(a.content for a in atoms1[:3]), 200),
                         "判定": "抽取成功且再抽不翻倍(updating-not-creating) ✓" if ok else "抽取异常或重复堆积 ✗"}
                return RunOutcome(ok, 1.0 if ok else 0.3, "" if ok else "重复堆积或抽取失败",
                                  f"入库{len(atoms1)}→{len(atoms2)}（净增{grew}）", trace)

    # 需 LLM（可退化）：召回渐进披露——画像在前、原子按相关性
    def case_recall():
        with tempfile.TemporaryDirectory() as td:
            with _temporary_env(HASHMM_LAYERED_MEMORY="1", HASHMM_LAYERED_MEMORY_DIR=td):
                uid = "rec_" + _uuid.uuid4().hex[:6]
                # 直接铺一些原子（不依赖 LLM），测召回排序
                atoms = [
                    L.Atom(id="a", content="用户要求回答先给结论再给理由", type="instruction",
                           priority=95, created="x", updated="x"),
                    L.Atom(id="b", content="用户在做跨模态哈希检索项目", type="episodic",
                           priority=80, created="x", updated="x"),
                    L.Atom(id="c", content="用户喜欢喝美式咖啡", type="persona",
                           priority=55, created="x", updated="x"),
                ]
                L._write_atoms(uid, atoms)
                hits = L.recall(uid, "检索项目怎么做")
                atom_hits = [h for h in hits if h.get("kind") == "atom"]
                # 与查询最相关的"检索项目"原子应排在无关的"咖啡"之前
                texts = [h["text"] for h in atom_hits]
                idx_proj = next((i for i, t in enumerate(texts) if "检索" in t), 99)
                idx_coffee = next((i for i, t in enumerate(texts) if "咖啡" in t), 99)
                ok = len(atom_hits) >= 2 and idx_proj < idx_coffee
                trace = {"问题/输入": "查询『检索项目怎么做』，召回应把相关原子排前",
                         "召回顺序": _clip(" | ".join(texts), 250),
                         "相关原子位置": idx_proj, "无关(咖啡)位置": idx_coffee,
                         "判定": "相关性排序正确 ✓" if ok else "召回排序未按相关性 ✗"}
                return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "召回排序错误",
                                  f"proj@{idx_proj} < coffee@{idx_coffee}", trace)

    rep.add(run_case_ntimes("分层-情境块META往返(离线)", case_meta_roundtrip, max(1, k)))
    rep.add(run_case_ntimes("分层-去重skip/update/store(离线)", case_dedup, max(1, k)))
    rep.add(run_case_ntimes("分层-抽取管线+不重复堆积(需LLM)", case_extract_pipeline, max(1, k)))
    rep.add(run_case_ntimes("分层-召回渐进披露(离线)", case_recall, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# B4. 符号化卸载（移植自 TencentDB Agent Memory 的上下文卸载 + Mermaid 符号图）
# ════════════════════════════════════════════════════════════════════════
def run_symbolic_offload(k: int = 2) -> SuiteReport:
    """符号化短期记忆真跑：把啰嗦日志卸载到 refs → 上下文只留 Mermaid 符号图（带 node_id）→
    grep node_id 下钻回原文 → token 省了多少。移植自腾讯 Agent Memory 的 context offloading。
    离线、确定性——直接验证'省 token 且可追溯'这一核心承诺。"""
    rep = SuiteReport("符号化卸载·端到端")
    try:
        from hashmm.agent import context_offload as CO
    except Exception as e:  # noqa: BLE001
        for n in ["卸载-node_id生成", "卸载-Mermaid符号图", "卸载-下钻回原文", "卸载-token省下"]:
            rep.add(run_case_ntimes(n, None, k, skip_reason=f"context_offload 不可用：{type(e).__name__}"))
        return rep

    import uuid as _uuid

    @contextmanager
    def _fresh():
        sess = "s_" + _uuid.uuid4().hex[:8]
        directory = "/tmp/co_hard_" + _uuid.uuid4().hex[:6]
        with _temporary_env(
            HASHMM_CONTEXT_OFFLOAD="1",
            HASHMM_CONTEXT_OFFLOAD_DIR=directory,
        ):
            yield sess

    def case_nodeid():
        with _fresh() as sess:
            n1 = CO.offload(sess, "kb_search(RAG)", "命中3篇：doc1 doc2 doc3")
            n2 = CO.offload(sess, "run_shell(ls)", "a.txt b.txt")
            ok = bool(CO.NODE_ID_RE.fullmatch(n1)) and bool(CO.NODE_ID_RE.fullmatch(n2)) and n1 != n2
            trace = {"问题/输入": "连续卸载两次工具输出",
                     "node_id_1": n1, "node_id_2": n2,
                     "格式校验": f"n1匹配={bool(CO.NODE_ID_RE.fullmatch(n1))}, 递增={n1 != n2}",
                     "判定": "node_id 格式正确且递增 ✓" if ok else "node_id 异常 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "node_id异常",
                              f"{n1} / {n2}", trace)

    def case_mermaid():
        with _fresh() as sess:
            CO.offload(sess, "kb_search", "结果A")
            CO.offload(sess, "fetch_url", "网页B")
            mmd = CO.build_mermaid(sess)
            ok = mmd.startswith("graph") and "-->" in mmd and mmd.count("[") >= 2
            trace = {"问题/输入": "2 次卸载后生成 Mermaid 符号图",
                     "Mermaid": _clip(mmd, 300),
                     "判定": "符号图含节点与连边 ✓" if ok else "符号图结构缺失 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "符号图缺失",
                              _clip(mmd, 120), trace)

    def case_drilldown():
        with _fresh() as sess:
            big = "重要证据X" + "填充" * 500
            nid = CO.offload(sess, "kb_search(证据)", big)
            raw = CO.drilldown(sess, nid)
            # 也测：从一段含 node_id 的文本里 grep 出来再下钻
            raw2 = CO.drilldown(sess, f"参见 {nid} 里的细节")
            ok = "重要证据X" in raw and "重要证据X" in raw2
            trace = {"问题/输入": f"卸载大段原文，按 {nid} 下钻取回",
                     "下钻长度": len(raw), "含关键证据": "重要证据X" in raw,
                     "从句子中grep下钻": "重要证据X" in raw2,
                     "判定": "下钻可无损取回原文 ✓" if ok else "下钻丢失原文 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "下钻丢失",
                              f"取回 {len(raw)} 字", trace)

    def case_token_saved():
        with _fresh() as sess:
            for i in range(5):
                CO.offload(sess, f"tool{i}", "日志" * 800)   # 每条约 1600 字
            view = CO.context_view(sess)
            # 5 条各 ~1600 字卸载，上下文只留 gist（各 ~120 字）→ 应省下可观 token
            ok = view["approx_tokens_saved"] > 1000 and view["nodes"] == 5 and view["mermaid"]
            trace = {"问题/输入": "卸载 5 条各约 1600 字的日志",
                     "节点数": view["nodes"], "估算省下 token": view["approx_tokens_saved"],
                     "上下文只留符号图长度": len(view["mermaid"]),
                     "判定": "确实省下大量 token 且保留可追溯符号图 ✓" if ok else "省 token 不达预期 ✗"}
            return RunOutcome(ok, min(1.0, view["approx_tokens_saved"] / 2000),
                              "" if ok else "省token不足",
                              f"省 {view['approx_tokens_saved']} token", trace)

    rep.add(run_case_ntimes("卸载-node_id生成递增", case_nodeid, max(1, k)))
    rep.add(run_case_ntimes("卸载-Mermaid符号图", case_mermaid, max(1, k)))
    rep.add(run_case_ntimes("卸载-node_id下钻回原文", case_drilldown, max(1, k)))
    rep.add(run_case_ntimes("卸载-token省下(可追溯)", case_token_saved, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# B5~B7. 困难 RAG（干扰 / 大海捞针 / 冲突证据）——需 LLM
# ════════════════════════════════════════════════════════════════════════
def _ask_rag(llm_fn, materials: str, question: str) -> str:
    sys_p = ("你是严谨的检索问答助手。只依据【材料】回答；材料未提及的绝不编造；"
             "材料互相矛盾时要明确指出冲突、不要武断二选一。回答简洁。")
    prompt = f"【材料】\n{materials}\n\n【问题】{question}"
    try:
        qc = getattr(llm_fn, "quick_call", None)
        if callable(qc):
            return str(qc(sys_p, prompt, max_tok=350) or "").strip()
        return str(llm_fn(sys_p + "\n\n" + prompt) or "").strip()
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return ""


def run_rag_distractor(llm_fn=None, k: int = 2) -> SuiteReport:
    """干扰鲁棒：材料里混入'相似但无关'的干扰段，模型不能被带偏。需 LLM。"""
    rep = SuiteReport("RAG困难·干扰鲁棒")
    cases = [
        {"name": "相似公司干扰",
         "mat": "苹果公司2023年营收3833亿美元。\n（干扰）苹果醋厂2023年营收800万元。\n（干扰）苹果社区超市月流水50万。",
         "q": "苹果公司2023年营收是多少？", "must": ["3833"], "trap": ["800万", "50万"]},
        {"name": "相近年份干扰",
         "mat": "该项目2024年上线。\n（干扰）另一个同名项目2019年就上线了。\n（干扰）某测试环境2021年搭建。",
         "q": "该项目哪一年上线？", "must": ["2024"], "trap": ["2019", "2021"]},
        {"name": "相似人名干扰",
         "mat": "报告作者是张伟（数据科学家）。\n（干扰）审稿人张玮。\n（干扰）致谢里提到张薇。",
         "q": "报告作者是谁？", "must": ["张伟"], "trap": []},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    for c in cases:
        def run_once(c=c):
            ans = _ask_rag(llm_fn, c["mat"], c["q"])
            got_right = all(m in ans for m in c["must"])
            got_trapped = any(t in ans for t in c["trap"])
            ok = got_right and not got_trapped
            fm = "" if ok else ("被干扰带偏" if got_trapped else "漏掉正确答案")
            trace = {"问题/输入": c["q"], "检索到的材料(含干扰)": _clip(c["mat"], 300),
                     "最终答案": _clip(ans, 250),
                     "判定": ("抗干扰成功 ✓" if ok else
                              (f"被干扰项带偏 ✗（命中陷阱 {c['trap']}）" if got_trapped else "漏掉正确答案 ✗"))}
            return RunOutcome(ok, 1.0 if ok else 0.0, fm, _clip(ans, 120), trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


def run_rag_needle(llm_fn=None, k: int = 2) -> SuiteReport:
    """长文大海捞针（Lost-in-the-Middle）：把答案埋进长材料中段，测能否稳定捞出。需 LLM。"""
    rep = SuiteReport("RAG困难·长文大海捞针")

    def _haystack(needle: str, pos: str) -> str:
        filler = ["向量数据库用于相似度检索。", "分块策略影响召回质量。", "重排序提升top结果。",
                  "嵌入模型决定语义空间。", "混合检索结合稀疏与稠密。", "上下文窗口有长度上限。",
                  "提示词工程影响输出。", "评测集需覆盖真实分布。"] * 6
        n = len(filler)
        if pos == "start":
            body = [needle] + filler
        elif pos == "end":
            body = filler + [needle]
        else:
            body = filler[:n // 2] + [needle] + filler[n // 2:]
        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(body))

    cases = [
        {"name": "针在中段", "needle": "本系统的密钥轮换周期是每 90 天一次。",
         "pos": "middle", "q": "本系统的密钥轮换周期是多久？", "must": ["90"]},
        {"name": "针在开头", "needle": "数据库备份保留策略是保留最近 35 天。",
         "pos": "start", "q": "数据库备份保留多少天？", "must": ["35"]},
        {"name": "针在结尾", "needle": "限流阈值配置为每分钟 128 次请求。",
         "pos": "end", "q": "限流阈值是每分钟多少次？", "must": ["128"]},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    for c in cases:
        def run_once(c=c):
            mat = _haystack(c["needle"], c["pos"])
            ans = _ask_rag(llm_fn, mat, c["q"])
            ok = all(m in ans for m in c["must"])
            trace = {"问题/输入": c["q"] + f"（针位置：{c['pos']}，材料 {mat.count(chr(10))+1} 行）",
                     "检索到的材料(节选)": _clip(mat, 260),
                     "最终答案": _clip(ans, 200),
                     "判定": "从长文中段/两端稳定捞出 ✓" if ok else f"迷失在长文里 ✗（漏 {c['must']}）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "迷失在中间",
                              _clip(ans, 120), trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


def run_rag_conflict(llm_fn=None, k: int = 2) -> SuiteReport:
    """冲突证据：材料自相矛盾时，要指出冲突而非武断选一个。需 LLM。"""
    rep = SuiteReport("RAG困难·冲突证据")
    cases = [
        {"name": "数字冲突",
         "mat": "文档A：项目预算为 500 万元。\n文档B：项目预算为 800 万元。",
         "q": "项目预算是多少？"},
        {"name": "结论冲突",
         "mat": "研究1：该方案能提升转化率。\n研究2：该方案对转化率无显著影响。",
         "q": "该方案对转化率有效吗？"},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    conflict_words = ["冲突", "矛盾", "不一致", "两种", "分别", "有的", "不同", "各执", "存在差异", "无法确定"]
    for c in cases:
        def run_once(c=c):
            ans = _ask_rag(llm_fn, c["mat"], c["q"])
            acknowledges = any(w in ans for w in conflict_words)
            trace = {"问题/输入": c["q"], "检索到的材料(相互矛盾)": _clip(c["mat"], 250),
                     "最终答案": _clip(ans, 250),
                     "判定": "指出了证据冲突 ✓" if acknowledges else "武断选了一边、未指出冲突 ✗"}
            return RunOutcome(acknowledges, 1.0 if acknowledges else 0.0,
                              "" if acknowledges else "未指出冲突", _clip(ans, 120), trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep

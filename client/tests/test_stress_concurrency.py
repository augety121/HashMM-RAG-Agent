"""tests/test_stress_concurrency.py — 大厂级并发压力测试（V306）。

不同于此前"纯内存小数据"的浅测，这里用**真实线程竞态 + 真实 SQLite IO + 故障注入**去打
有状态并发原语，验证生产级不变量：
  · 幂等库 check_and_reserve：并发抢同一键必须 exactly-once（新键 + 过期键两条路径）——
    这是本套件**已暴露并修复**的真实并发重复执行 bug（SELECT→INSERT 非原子 / 过期踩踏）。
  · 幂等库故障注入：DB 不可用时 fail-open（返回 None 不崩、不卡死）。
  · 幂等 txlog：多线程并发写事务日志不丢不错。
  · LRUCache：高并发 get/set/delete/evict 下 max_size 不越界、无损坏、TTL 正确。

沙箱可直接跑（纯 stdlib + 项目内模块）。用 barrier 制造最大争用，多轮取最坏值。
"""
import importlib
import os
import sys as _sys
import tempfile
import threading
import time
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _fresh_idem():
    os.environ["HASHMM_DATA_DIR"] = tempfile.mkdtemp()
    from hashmm.agent import idempotency as I
    return importlib.reload(I)


def _hammer_same_key(I, key, op, n):
    """n 个线程用 barrier 同时抢同一键，返回拿到执行权(None)的线程数。"""
    res, lk, bar = [], threading.Lock(), threading.Barrier(n)

    def w():
        bar.wait()
        r = I.check_and_reserve(key, op)
        with lk:
            res.append(r)

    ts = [threading.Thread(target=w) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return sum(1 for r in res if r is None)


# ══════════════════════════════ 幂等：exactly-once ══════════════════════════════
def test_idempotency_fresh_key_exactly_once():
    """并发抢一个全新键 → 只有一个线程拿到执行权（其余命中占位跳过）。"""
    I = _fresh_idem()
    worst = 0
    for it in range(12):
        key = I.make_key("write", f"target_{it}")
        worst = max(worst, _hammer_same_key(I, key, "write", 40))
    assert worst == 1, f"新键并发重复执行：worst={worst}（应 exactly-once）"


def test_idempotency_expired_key_no_stampede():
    """键过期一次后并发抢 → 只有一个线程刷新执行，其余命中新占位。防缓存过期踩踏。"""
    I = _fresh_idem()
    I._TTL_SECONDS = 0.4
    worst = 0
    for it in range(10):
        key = I.make_key("refresh", f"k_{it}")
        I.check_and_reserve(key, "refresh")
        I.commit(key, {"v": 1})
        time.sleep(0.45)   # 过期一次
        worst = max(worst, _hammer_same_key(I, key, "refresh", 40))
    assert worst == 1, f"过期键并发踩踏：worst={worst} 个线程重复执行（应 exactly-once）"


def test_idempotency_hit_returns_committed_result():
    """占位后 commit，再次 check 应命中并拿回结果（不重复执行）。"""
    I = _fresh_idem()
    key = I.make_key("op", "x")
    assert I.check_and_reserve(key, "op") is None          # 首次：执行权
    I.commit(key, {"answer": 42})
    hit = I.check_and_reserve(key, "op")
    assert hit is not None and hit.get("hit") is True
    assert hit.get("result") == {"answer": 42}, f"命中未拿回已提交结果：{hit}"


def test_idempotency_release_allows_retry():
    """失败后 release，下次应能重新执行（占位被清）。"""
    I = _fresh_idem()
    key = I.make_key("op", "y")
    assert I.check_and_reserve(key, "op") is None
    I.release(key)                                          # 失败释放
    assert I.check_and_reserve(key, "op") is None, "release 后未能重试"


# ══════════════════════════════ 幂等：故障注入 ══════════════════════════════
def test_idempotency_fails_closed_on_db_error(monkeypatch=None):
    """DB 连接抛错时不能把“未知”伪装成新的写入执行权。"""
    I = _fresh_idem()
    import sqlite3 as _sq
    orig = _sq.connect

    def boom(*a, **k):
        raise _sq.OperationalError("disk I/O error (injected)")

    _sq.connect = boom
    try:
        import pytest
        with pytest.raises(I.IdempotencyUnavailable):
            I.check_and_reserve(I.make_key("op", "z"), "op")
    finally:
        _sq.connect = orig


def test_idempotency_commit_fails_closed(monkeypatch):
    """A lost commit must be surfaced as uncertain, never as normal success."""
    I = _fresh_idem()
    key = I.make_key("op", "commit-failure")
    assert I.check_and_reserve(key, "op") is None

    def boom():
        raise OSError("disk full (injected)")

    monkeypatch.setattr(I, "_db", boom)
    import pytest
    with pytest.raises(I.IdempotencyCommitUnavailable):
        I.commit(key, {"status": "ok"})


def test_idempotency_txlog_concurrent_no_loss():
    """多线程并发写事务日志 → 全部落库不丢。"""
    I = _fresh_idem()
    task = "task_concurrent"
    N = 30

    def w(i):
        I.log_step(task, i, "write", f"file_{i}", True, "ok")

    ts = [threading.Thread(target=w, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = I.get_transaction(task)
    assert len(rows) == N, f"并发事务日志丢失：{len(rows)}/{N}"


# ══════════════════════════════ LRUCache：并发不变量 ══════════════════════════════
def test_lru_cache_size_invariant_under_load():
    """20 线程 × 5000 混合操作：max_size 绝不越界、无异常。"""
    import random
    from hashmm.agent.cache import LRUCache
    c = LRUCache(max_size=100, default_ttl=300)
    errors = []

    def w(tid):
        try:
            for i in range(5000):
                k = f"k{random.randint(0, 300)}"
                if random.random() < 0.5:
                    c.set(k, tid * 100000 + i)
                else:
                    c.get(k)
                if random.random() < 0.05:
                    c.delete(f"k{random.randint(0, 300)}")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=w, args=(t,)) for t in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"并发下 LRUCache 抛错：{errors[:3]}"
    assert len(c._cache) <= 100, f"max_size 越界：{len(c._cache)}>100"


def test_lru_cache_ttl_expiry():
    """TTL 到期的条目不得再被返回。"""
    from hashmm.agent.cache import LRUCache
    c = LRUCache(max_size=10, default_ttl=300)
    c.set("k", "v", ttl=0.2)
    assert c.get("k") == "v"
    time.sleep(0.25)
    assert c.get("k") is None, "TTL 到期后仍返回旧值"


def test_lru_cache_eviction_order():
    """超容量时淘汰最久未用（LRU 语义）。"""
    from hashmm.agent.cache import LRUCache
    c = LRUCache(max_size=3, default_ttl=300)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.get("a")            # a 变最近使用
    c.set("d", 4)         # 应淘汰 b（最久未用）
    assert c.get("b") is None, "LRU 淘汰顺序错误：b 应被淘汰"
    assert c.get("a") == 1 and c.get("c") == 3 and c.get("d") == 4


# ══════════════════════════════ 黑板 / 任务队列：并发一致性 ══════════════════════════════
def test_global_workspace_concurrent_consistency():
    """4000 并发提交：seq 唯一（无丢增量）、模块计数一致、history 有界（deque maxlen 防内存泄漏）。"""
    _fresh_idem()   # 只为设置临时 HASHMM_DATA_DIR
    from hashmm.agent import global_workspace as G
    import importlib as _il
    _il.reload(G)
    gw = G.GlobalWorkspace()
    N, PER = 20, 200

    def w(tid):
        for i in range(PER):
            gw.submit(f"mod{tid}", "event", f"s{i}", salience=0.6)

    ts = [threading.Thread(target=w, args=(t,)) for t in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    seqs = [e["seq"] for e in gw._history]
    assert len(set(seqs)) == len(seqs), "并发下 seq 出现重复（丢失自增互斥）"
    tot = sum(m["counts"]["events"] for m in gw._modules.values())
    assert tot == N * PER, f"模块事件计数丢失：{tot} vs {N * PER}"
    assert len(gw._history) <= G._HISTORY_CAP, f"history 无界增长（内存泄漏）：{len(gw._history)}"


def test_dispatch_no_double_claim():
    """100 任务 + 30 线程并发认领：每个任务恰好被认领一次（无重复领取）。"""
    _fresh_idem()
    from hashmm.agent import dispatch as D
    import importlib as _il
    _il.reload(D)
    TASKS = 100
    for i in range(TASKS):
        D.create_task("r1", "work", {"i": i})
    claimed, lk = [], threading.Lock()

    def w():
        while True:
            t = D.poll("r1")
            if not t:
                break
            with lk:
                claimed.append(t["task_id"])

    ts = [threading.Thread(target=w) for _ in range(30)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(claimed) == len(set(claimed)) == TASKS, \
        f"任务重复领取：claimed={len(claimed)} unique={len(set(claimed))} tasks={TASKS}"


def test_team_canvas_concurrent_no_lost_update():
    """多智能体：40 个角色并发标记完成 → 画布上每个角色槽都更新、进度计数正确，无丢失更新。
    验证 team._rewrite 的 _FILE_LOCK 真的挡住了并行角色对同一画布文件的读改写竞态。"""
    import pathlib
    import re
    _fresh_idem()
    from hashmm.agent import team as T
    import importlib as _il
    _il.reload(T)
    from hashmm.api import database as db
    tmpdir = tempfile.mkdtemp()
    db.conv_files_dir = lambda conv_id: pathlib.Path(tmpdir)
    N = 40
    fname = "canvas.html"
    slots = "".join(
        f"<span class='st st-wait' id='tm-{i}'>等待</span><div class='fd' id='tf-{i}'></div>"
        for i in range(N))
    (pathlib.Path(tmpdir) / fname).write_text(
        f"<html><b id='tm-prog'>0</b>{slots}</html>", encoding="utf-8")

    bar = threading.Barrier(N)

    def w(i):
        bar.wait()
        T._mark("c1", fname, i, "ok", finding=f"role {i} done")

    ts = [threading.Thread(target=w, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    final = (pathlib.Path(tmpdir) / fname).read_text(encoding="utf-8")
    ok_count = final.count("class='st st-ok'")
    prog = re.search(r"<b id='tm-prog'>(\d+)</b>", final)
    prog_val = int(prog.group(1)) if prog else -1
    assert ok_count == N, f"并发标记丢失更新：st-ok={ok_count} 应={N}"
    assert prog_val == N, f"进度计数错误：{prog_val} 应={N}"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 62)
    print("大厂级并发压力测试（V306：真线程竞态 + 故障注入 + 不变量）")
    print("=" * 62)
    p = f = 0
    t0 = time.time()
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
            p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}")
            f += 1
    print("=" * 62)
    print(f"结果：PASS={p}  FAIL={f}  用时 {time.time() - t0:.1f}s")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())

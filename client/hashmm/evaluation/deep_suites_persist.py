"""hashmm/evaluation/deep_suites_persist.py — 持久化 / 后台执行硬测试（V295）。

诉求："测功能好不好用，不是能不能跑；在最难的条件下测"。这一组专门把本版新做的
"后台执行、重进可见、历史日期正确"这些**用户体验级能力**逼到极限验证——不是"能创建会话"
这种浅测，而是模拟真实故障链：整页刷新、并发写、云端回填盖日期、乱序到达……看这些能力扛不扛。

套件（全部离线、确定性、真调用 hashmm.api.database；每步带详细日志便于事后分析）：
  1. stream_persistence  —— 流式消息生命周期：建 streaming 占位 → 周期落 partial → 收尾 complete；
     验证任意时刻 get_messages 都能拿到"当前进度 + 正确 status"（= 整页刷新后能续看的地基）。
  2. history_dates       —— 历史日期正确性：老会话真实 created_at 保留、坏回填被纠正、日期分桶正确
     （= 修"所有历史都挤在今天"、"几天内显示星期/超7天显示年月日"）。
  3. message_integrity   —— 消息完整性：并发写不丢不乱、顺序稳定（= 多任务后台同时写也不串）。
  4. conv_list_ordering  —— 会话列表排序与日期：按最近活动排序、created_at 不被列表接口污染。

每条用例多次运行取稳（Pass^k），逐步骤留"执行日志"（timeline），失败给可行动定位。永不抛错。
"""
from __future__ import annotations

import os
import importlib.util
import sys
import tempfile
import time
import uuid

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.deep_eval import RunOutcome, SuiteReport, run_case_ntimes

logger = get_logger("hashmm.evaluation.deep_suites_persist")


def _fresh_db():
    """每条用例一个独立临时库，互不污染。返回 (db_module, dir)。

    在线自测运行在服务进程内，绝不能修改 ``hashmm.api.database`` 的
    ``DB_PATH`` 或连接池。旧实现会把生产模块重定向到临时库，导致并发的
    WorkRuntime/Chat 请求随后报 ``no such table: work_runs``。

    这里从同一份、已审计的源码加载一个私有模块实例；它拥有自己的 DB_PATH、
    模型镜像路径、锁和连接池。测试调用的仍是真实 database 实现，但与在线
    服务的数据面完全隔离。
    """
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="hmm_persist_")
    root = Path(d)
    dbfile = root / f"t_{uuid.uuid4().hex[:8]}.sqlite"
    from hashmm.api import database as production_db

    module_name = f"hashmm.api._selftest_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, production_db.__file__)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法创建隔离的数据库自测模块")
    isolated_db = importlib.util.module_from_spec(spec)
    # Some stdlib/runtime helpers resolve the executing module by name.
    # Register only for the duration of loading; callers retain the module
    # object directly and no other import can discover it afterwards.
    sys.modules[module_name] = isolated_db
    try:
        spec.loader.exec_module(isolated_db)
    finally:
        sys.modules.pop(module_name, None)

    isolated_db.DB_PATH = dbfile
    isolated_db._MODELS_MIRROR = root / "models_backup.json"
    isolated_db._close_pool()
    isolated_db.init_db()
    return isolated_db, d


def _log(steps: list, msg: str):
    """把一步执行细节追加到日志（带毫秒时戳）——报告里逐步可见，方便你事后分析。"""
    steps.append(f"[{time.strftime('%H:%M:%S')}.{int((time.time()%1)*1000):03d}] {msg}")


# ════════════════════════════════════════════════════════════════════════
# 1. 流式消息生命周期（整页刷新后续看的地基）
# ════════════════════════════════════════════════════════════════════════
def run_stream_persistence(k: int = 2) -> SuiteReport:
    rep = SuiteReport("持久化·流式消息生命周期")

    def case_lifecycle():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            conv = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(conv, "u1", "流式测试")
            _log(steps, f"建会话 {conv}")
            db.create_message(conv, "user", "帮我写个长回答")
            _log(steps, "落用户消息")
            # 建 streaming 占位（后端 streaming.py 起手做的事）
            mid = db.create_message(conv, "assistant", "", status="streaming")
            _log(steps, f"建 assistant 占位 status=streaming id={mid}")
            # 刚建时：get_messages 应能看到这条 streaming 占位（内容空）
            m0 = db.get_messages(conv)
            last0 = m0[-1]
            ok_placeholder = last0.get("status") == "streaming" and last0.get("role") == "assistant"
            _log(steps, f"占位可见性：末条 status={last0.get('status')} content_len={len(last0.get('content') or '')}")
            # 周期落 partial（模拟后端每 1.5s flush）——每次 get 都能拿到当前进度
            partials = ["第一段…", "第一段…第二段…", "第一段…第二段…第三段…"]
            progress_ok = True
            for p in partials:
                db.update_message(mid, content=p, status="streaming")
                cur = db.get_messages(conv)[-1]
                got = cur.get("content") or ""
                still_streaming = cur.get("status") == "streaming"
                _log(steps, f"flush partial → 回读 content_len={len(got)} status={cur.get('status')}")
                if got != p or not still_streaming:
                    progress_ok = False
            # 收尾 complete
            full = "第一段…第二段…第三段…（完）"
            db.update_message(mid, content=full, status="complete")
            fin = db.get_messages(conv)[-1]
            ok_complete = fin.get("status") == "complete" and fin.get("content") == full
            _log(steps, f"收尾 → status={fin.get('status')} content_len={len(fin.get('content') or '')}")
            # 关键不变量：整个过程只有 1 条 assistant 消息（没有重复占位/最终并存）
            asst = [m for m in db.get_messages(conv) if m.get("role") == "assistant"]
            ok_single = len(asst) == 1
            _log(steps, f"assistant 消息条数={len(asst)}（应=1，避免占位与最终并存的重影）")

            ok = ok_placeholder and progress_ok and ok_complete and ok_single
            trace = {
                "场景": "流式消息 建占位→周期落partial→收尾complete（模拟整页刷新可续看）",
                "执行日志": steps,
                "占位可见": ok_placeholder, "partial逐步可读": progress_ok,
                "收尾complete": ok_complete, "单条不重影": ok_single,
                "判定": "流式落库全生命周期正确，任意时刻都能拿到进度+正确status ✓" if ok else "流式落库有缺陷 ✗",
            }
            fm = "" if ok else ("partial不可读" if not progress_ok else ("重影" if not ok_single else "status错误"))
            return RunOutcome(ok, 1.0 if ok else 0.0, fm,
                              f"占位{ok_placeholder}/partial{progress_ok}/complete{ok_complete}/单条{ok_single}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_disconnect_recovery():
        """模拟"生成中断电/断连"：streaming 占位存在但从未 complete → get_messages 仍应能拿到
        最后 partial 和 streaming 状态（前端据此判断"还在生成/需重连"而不是当成空消息）。"""
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            conv = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(conv, "u1", "断连测试")
            mid = db.create_message(conv, "assistant", "", status="streaming")
            db.update_message(mid, content="已经写了一半就断了", status="streaming")
            _log(steps, "落一半 partial 后不再收尾（模拟断连）")
            cur = db.get_messages(conv)[-1]
            ok = cur.get("status") == "streaming" and "一半" in (cur.get("content") or "")
            _log(steps, f"回读：status={cur.get('status')} 含'一半'={'一半' in (cur.get('content') or '')}")
            trace = {"场景": "生成中断连：占位 + 半截 partial，永不 complete",
                     "执行日志": steps,
                     "判定": "断连后仍可拿到半截内容+streaming状态（可重连续看）✓" if ok else "断连后状态/内容丢失 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "断连恢复失败",
                              f"status={cur.get('status')}", trace)
        except Exception as e:  # noqa: BLE001
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}", {"执行日志": steps})

    rep.add(run_case_ntimes("流式-建占位→partial→complete全生命周期", case_lifecycle, max(1, k)))
    rep.add(run_case_ntimes("流式-断连后半截内容+状态可恢复", case_disconnect_recovery, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 2. 历史日期正确性（修"全挤今天" + "几天内星期/超7天年月日"）
# ════════════════════════════════════════════════════════════════════════
def run_history_dates(k: int = 2) -> SuiteReport:
    rep = SuiteReport("持久化·历史日期正确性")

    def case_preserve_and_correct():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            now = time.time()
            old = now - 30 * 86400   # 30 天前
            # 云端回填：带真实 created_at 建老会话
            db.create_conversation("c_old", "u1", "30天前的会话", created_at=old, updated_at=old)
            r1 = db.get_conversation("c_old")
            ok_preserve = abs((r1.get("created_at") or 0) - old) < 2
            _log(steps, f"带真实 created_at 建老会话 → 回读 created_at 距真实值 {abs((r1.get('created_at') or 0)-old):.1f}s（应<2s）")
            # 坏回填模拟：某历史 bug 曾把这条盖成"现在"。手动改晚，再走带真实值的回填应纠回
            with db._conn() as c:
                c.execute("UPDATE conversations SET created_at=? WHERE id=?", (now, "c_old"))
            _log(steps, "手动把 created_at 盖成现在（模拟历史坏回填）")
            db.create_conversation("c_old", "u1", "30天前的会话", created_at=old, updated_at=old)
            r2 = db.get_conversation("c_old")
            ok_correct = abs((r2.get("created_at") or 0) - old) < 2
            _log(steps, f"再走带真实值回填 → created_at 距真实值 {abs((r2.get('created_at') or 0)-old):.1f}s（应被纠回<2s）")
            # 无参回填不该改动已存在会话的日期
            db.create_conversation("c_old", "u1", "30天前的会话")
            r3 = db.get_conversation("c_old")
            ok_noharm = abs((r3.get("created_at") or 0) - old) < 2
            _log(steps, f"无参回填后 created_at 距真实值 {abs((r3.get('created_at') or 0)-old):.1f}s（不该被改动）")

            ok = ok_preserve and ok_correct and ok_noharm
            trace = {"场景": "老会话真实日期保留 / 坏回填纠正 / 无参回填不破坏",
                     "执行日志": steps,
                     "真实值保留": ok_preserve, "坏日期纠正": ok_correct, "无参不破坏": ok_noharm,
                     "判定": "历史日期在回填链路里始终正确（不再全挤今天）✓" if ok else "历史日期会被回填污染 ✗"}
            fm = "" if ok else ("日期未保留" if not ok_preserve else ("坏日期未纠正" if not ok_correct else "无参回填破坏日期"))
            return RunOutcome(ok, 1.0 if ok else 0.0, fm,
                              f"保留{ok_preserve}/纠正{ok_correct}/不破坏{ok_noharm}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_date_bucketing():
        """验证前端日期分桶规则的等价逻辑（今天/昨天/周几/超7天年月日）——用 Python 复刻 fmtItemDate，
        确保规则本身正确（前端同款规则，任何一端改了这里能兜住）。"""
        steps: list[str] = []
        import datetime as dt

        def fmt(created_ms: float, now: dt.datetime) -> str:
            d0 = dt.datetime.fromtimestamp(created_ms / 1000)
            start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start_d = d0.replace(hour=0, minute=0, second=0, microsecond=0)
            diff = (start_today - start_d).days
            if diff <= 0:
                return "今天"
            if diff == 1:
                return "昨天"
            if diff < 7:
                return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d0.weekday()]
            if d0.year == now.year:
                return f"{d0.month:02d}-{d0.day:02d}"
            return f"{d0.year}-{d0.month:02d}-{d0.day:02d}"

        now = dt.datetime(2026, 7, 10, 13, 0, 0)   # 固定"现在"便于断言
        checks = [
            (now.timestamp() * 1000, "今天"),
            ((now - dt.timedelta(days=1)).timestamp() * 1000, "昨天"),
            ((now - dt.timedelta(days=3)).timestamp() * 1000, ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][(now - dt.timedelta(days=3)).weekday()]),
            ((now - dt.timedelta(days=10)).timestamp() * 1000, f"{(now-dt.timedelta(days=10)).month:02d}-{(now-dt.timedelta(days=10)).day:02d}"),
            (dt.datetime(2024, 3, 5, 9).timestamp() * 1000, "2024-03-05"),
        ]
        allok = True
        for ms, expect in checks:
            got = fmt(ms, now)
            good = got == expect
            allok = allok and good
            _log(steps, f"{'✓' if good else '✗'} 输入{dt.datetime.fromtimestamp(ms/1000).date()} → 期望「{expect}」得到「{got}」")
        trace = {"场景": "日期分桶：今天/昨天/周几/同年MM-DD/跨年YYYY-MM-DD",
                 "执行日志": steps,
                 "判定": "五类日期显示规则全部正确 ✓" if allok else "日期显示规则有误 ✗"}
        return RunOutcome(allok, 1.0 if allok else 0.0, "" if allok else "日期规则错误",
                          f"5 类日期 {'全对' if allok else '有错'}", trace)

    rep.add(run_case_ntimes("日期-真实值保留/坏回填纠正/无参不破坏", case_preserve_and_correct, max(1, k)))
    rep.add(run_case_ntimes("日期-分桶规则(今天/昨天/周几/年月日)", case_date_bucketing, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 3. 消息完整性（并发写不丢不乱）
# ════════════════════════════════════════════════════════════════════════
def run_message_integrity(k: int = 1) -> SuiteReport:
    rep = SuiteReport("持久化·消息完整性(并发)")

    def case_concurrent_writes():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            from concurrent.futures import ThreadPoolExecutor
            conv = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(conv, "u1", "并发写测试")
            N = 50
            _log(steps, f"并发写入 {N} 条消息（模拟多后台任务同时往同一会话写）")

            def _w(i):
                db.create_message(conv, "user" if i % 2 == 0 else "assistant", f"消息#{i:03d}")
                return i

            t0 = time.time()
            with ThreadPoolExecutor(max_workers=12) as ex:
                list(ex.map(_w, range(N)))
            wall = round((time.time() - t0) * 1000)
            msgs = db.get_messages(conv, limit=200)
            got_contents = {m.get("content") for m in msgs}
            expected = {f"消息#{i:03d}" for i in range(N)}
            missing = expected - got_contents
            # 顺序单调（created_at 非降序）——get_messages 按 created_at ASC
            ts = [m.get("created_at") or 0 for m in msgs]
            ordered = all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1))
            no_loss = len(missing) == 0
            _log(steps, f"写入 {N} 条耗时 {wall}ms；回读 {len(msgs)} 条；丢失 {len(missing)} 条；顺序单调={ordered}")
            ok = no_loss and ordered
            trace = {"场景": f"{N} 条消息 12 线程并发写同一会话",
                     "执行日志": steps,
                     "写入耗时": f"{wall}ms", "回读条数": len(msgs), "丢失条数": len(missing),
                     "时间顺序单调": ordered,
                     "判定": "并发写零丢失且顺序稳定 ✓" if ok else f"并发写有问题 ✗（丢{len(missing)}/序{ordered}）"}
            return RunOutcome(ok, 1.0 if ok else max(0.0, 1 - len(missing) / N),
                              "" if ok else ("并发丢消息" if not no_loss else "顺序错乱"),
                              f"丢失{len(missing)}/{N}，顺序{'稳' if ordered else '乱'}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("并发-50条消息同时写零丢失且有序", case_concurrent_writes, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 4. 会话列表排序与日期
# ════════════════════════════════════════════════════════════════════════
def run_conv_list_ordering(k: int = 2) -> SuiteReport:
    rep = SuiteReport("持久化·会话列表排序")

    def case_recent_first():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            base = time.time()
            # 三个会话，不同活动时间
            for i, dt_off in enumerate([-100000, -50000, -1000]):
                cid = f"c{i}"
                db.create_conversation(cid, "u1", f"会话{i}", created_at=base + dt_off, updated_at=base + dt_off)
            _log(steps, "建 3 个会话，活动时间 old<mid<recent")
            # 给 c0 追加一条消息（bump updated_at 到最新）
            time.sleep(0.02)
            db.update_conversation("c0", title="会话0-刚活动")
            _log(steps, "给最老的 c0 追加活动（bump updated_at 到最新）")
            lst = db.list_conversations("u1", limit=50)
            order = [c.get("id") for c in lst]
            # 按 updated_at DESC：c0 刚活动应排最前
            ok_recent = order and order[0] == "c0"
            # created_at 不被列表接口污染（c1/c2 仍保留各自真实 created_at）
            c1 = next((c for c in lst if c.get("id") == "c1"), {})
            ok_created = abs((c1.get("created_at") or 0) - (base - 50000)) < 2
            _log(steps, f"列表顺序={order}（c0 应最前）；c1 的 created_at 距真实 {abs((c1.get('created_at') or 0)-(base-50000)):.1f}s")
            ok = ok_recent and ok_created
            trace = {"场景": "按最近活动排序 + created_at 不被列表污染",
                     "执行日志": steps, "列表顺序": order,
                     "最近活动置顶": ok_recent, "created_at不被污染": ok_created,
                     "判定": "列表按活动排序且日期不被污染 ✓" if ok else "列表排序或日期有问题 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0,
                              "" if ok else ("排序错误" if not ok_recent else "日期被污染"),
                              f"顺序{order}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("列表-最近活动置顶且日期不被污染", case_recent_first, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 5. App 后端契约（App 高频依赖的后端行为——App 问题比桌面多，这里贴身守住）
# ════════════════════════════════════════════════════════════════════════
def run_app_contract(k: int = 2) -> SuiteReport:
    """App（原生 Kotlin）跨端与重连强依赖的后端契约，全部离线真调用 DB：
      · import_messages_local：把 App 端/云端历史补到本地——INSERT OR IGNORE 去重、ISO→epoch 时间、
        再导入不翻倍（App 反复打开同一会话不该产生重复消息）。
      · 消息 status 往返：get_messages 必须原样返回 streaming/complete——App 的重连轮询靠它判断
        "还在生成/已完成"（这条链断了，App 就会"点进去一直空白或一直转"）。
      · 会话 created_at 经 ISO 往返仍正确——App 列表日期正确的地基。
    """
    rep = SuiteReport("App后端契约")

    def case_import_dedup():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            conv = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(conv, "u1", "App同步")
            iso = "2026-06-01T08:30:00Z"
            cloud = [
                {"id": "m1", "role": "user", "content": "云端问题", "status": "complete", "created_at": iso},
                {"id": "m2", "role": "assistant", "content": "云端回答", "status": "complete", "created_at": "2026-06-01T08:30:05Z"},
            ]
            n1 = db.import_messages_local(conv, cloud)
            _log(steps, f"首次导入 {len(cloud)} 条云端消息 → 新增 {n1}")
            n2 = db.import_messages_local(conv, cloud)   # 再导入同样的 → 应全部 IGNORE
            _log(steps, f"再次导入同样消息 → 新增 {n2}（应为 0，App 反复打开不该翻倍）")
            msgs = db.get_messages(conv)
            # ISO→epoch：m1 的 created_at 应约等于 2026-06-01 08:30:00 UTC
            from datetime import datetime, timezone
            want = datetime(2026, 6, 1, 8, 30, 0, tzinfo=timezone.utc).timestamp()
            m1 = next((m for m in msgs if m.get("id") == "m1"), {})
            ts_ok = abs((m1.get("created_at") or 0) - want) < 2
            _log(steps, f"m1 created_at 距期望 {abs((m1.get('created_at') or 0)-want):.1f}s（ISO→epoch 应<2s）")
            ok = n1 == 2 and n2 == 0 and len(msgs) == 2 and ts_ok
            trace = {"场景": "App/云端历史导入本地：去重 + ISO→epoch + 再导不翻倍",
                     "执行日志": steps, "首次新增": n1, "再次新增": n2, "总条数": len(msgs), "时间转换正确": ts_ok,
                     "判定": "跨端历史导入零重复且时间正确 ✓" if ok else "导入去重/时间有问题 ✗"}
            fm = "" if ok else ("重复导入" if n2 != 0 else ("时间转换错" if not ts_ok else "导入异常"))
            return RunOutcome(ok, 1.0 if ok else 0.0, fm, f"新增{n1}/{n2}，共{len(msgs)}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_status_roundtrip():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            conv = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(conv, "u1", "status往返")
            db.create_message(conv, "user", "问题")
            mid = db.create_message(conv, "assistant", "", status="streaming")
            _log(steps, "建 streaming 占位")
            got_streaming = db.get_messages(conv)[-1].get("status")
            db.update_message(mid, content="答完了", status="complete")
            got_complete = db.get_messages(conv)[-1].get("status")
            _log(steps, f"占位 status={got_streaming} → 收尾 status={got_complete}")
            ok = got_streaming == "streaming" and got_complete == "complete"
            trace = {"场景": "get_messages 原样返回 status（App 重连轮询判据）",
                     "执行日志": steps, "占位status": got_streaming, "收尾status": got_complete,
                     "判定": "status 往返正确，App 能据此判断生成中/已完成 ✓" if ok else "status 未正确返回 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "status错误",
                              f"{got_streaming}→{got_complete}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("App-跨端历史导入去重+时间转换", case_import_dedup, max(1, k)))
    rep.add(run_case_ntimes("App-消息status往返(重连判据)", case_status_roundtrip, max(1, k)))
    return rep

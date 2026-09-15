"""hashmm/evaluation/deep_suites_desktop.py — 桌面端专项硬测试（V296）。

诉求："测试桌面客户端的所有功能，测试一定要全面；测好不好用，不是能不能跑。"
这一组把桌面端四条核心链路逼到极限验证（全部离线、确定性、每步带毫秒时戳执行日志）：

  1. desktop_concurrency_guard —— 桌面并发原语回归守卫：直接读 desktop/main.js 源码，钉死
     V294 并发三件套（认领单飞/在飞计数/浏览器互斥锁）必须在位、旧 _dispatchBusy 单飞锁不得
     复活、轮询 2.5s 不得回退——谁改回"一个任务锁死整机"这里立刻红。
  2. dispatch_lifecycle —— 派活队列全生命周期（真队列，临时库）：建任务→认领(原子)→回填；
     **runner 掉线超时自愈回队**；跨 runner 隔离；重复回填幂等拒绝。桌面端"派活"的地基。
  3. team_canvas —— 多智能体画布：并行/流水线两版结构、实时时钟、角色耗时标签、着色正则
     可命中，以及 **XSS 逃逸**（目标含 <script> 必须被转义——画布是发布共享页，这条是安全底线）。
  4. activity_contract —— /activity 数据契约（App「客户端任务进度」+ 桌面任务可见性共同依赖）：
     streaming 会话出现、complete 消失、**跨用户隔离**、最新在前。

失败信息直接指到该查的文件与函数；永不抛错。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.deep_eval import RunOutcome, SuiteReport, run_case_ntimes
from hashmm.evaluation.deep_suites_persist import _fresh_db, _log

logger = get_logger("hashmm.evaluation.deep_suites_desktop")

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════════════════
# 1. 桌面并发原语回归守卫（静态契约：main.js 源码必须长这样）
# ════════════════════════════════════════════════════════════════════════
def run_desktop_concurrency_guard(k: int = 1) -> SuiteReport:
    rep = SuiteReport("桌面端·并发原语守卫")
    mj = _REPO_ROOT / "desktop" / "main.js"

    def case_primitives():
        steps: list[str] = []
        if not mj.exists():
            return RunOutcome(True, 0.0, "", "desktop/main.js 不在本部署（纯服务端），跳过",
                              {"判定": "skip", "路径": str(mj)})
        src = mj.read_text(encoding="utf-8", errors="ignore")
        lines = src.splitlines()
        code_lines = [ln for ln in lines
                      if not ln.lstrip().startswith("//") and not ln.lstrip().startswith("*")]
        code = "\n".join(code_lines)
        _log(steps, f"读取 desktop/main.js（{len(lines)} 行，剔除注释后检查代码行）")

        def _lineno(pat: str) -> str:
            """返回代码行（非注释）中该模式出现的行号清单——日志里直接给可跳转位置。"""
            hits = [str(i + 1) for i, ln in enumerate(lines)
                    if pat in ln and not ln.lstrip().startswith("//") and not ln.lstrip().startswith("*")]
            return ",".join(hits[:6]) + ("…" if len(hits) > 6 else "") if hits else "-"

        must_have = {
            "_withBrowserLock": "浏览器/电脑操作互斥锁（重任务彼此串行但不锁整机）",
            "_dispatchInflight": "在飞任务计数（并发池）",
            "_dispatchMaxConcurrent": "并发上限（可配 1-8）",
            "_dispatchPolling": "认领单飞标志（只单飞认领，不单飞执行）",
            "_execDispatchTask": "执行与认领脱钩的独立执行函数",
        }
        missing = [name for name in must_have if name not in code]
        for name, why in must_have.items():
            _log(steps, f"{'✓' if name not in missing else '✗ 缺失'} {name} @行[{_lineno(name)}] —— {why}")

        # 旧单飞锁不得复活（代码行里出现 `_dispatchBusy =` 或 `if (_dispatchBusy` 即回归）
        busy_regressed = bool(re.search(r"_dispatchBusy\s*=", code) or re.search(r"if\s*\(\s*_dispatchBusy", code))
        _log(steps, f"{'✗ 回归' if busy_regressed else '✓'} 旧 _dispatchBusy 单飞锁"
                    f"{'复活了（会重新锁死整机）@行[' + _lineno('_dispatchBusy') + ']' if busy_regressed else ' 未复活（仅注释提及属正常）'}")

        # 轮询 2.5s 不得回退成 5s 单飞时代
        poll_ok = "2500" in code and "_pollDispatchQueue" in code
        _log(steps, f"{'✓' if poll_ok else '✗'} 认领轮询 2500ms @行[{_lineno('2500')}]（空槽快速补位）")

        # 浏览器类分支确实经互斥锁执行
        lock_wired = bool(re.search(r"_withBrowserLock\s*\(\s*\(\)\s*=>\s*_handleBrowserAgent", code)) and \
                     bool(re.search(r"_withBrowserLock\s*\(\s*\(\)\s*=>\s*_handleAuto", code))
        _log(steps, f"{'✓' if lock_wired else '✗'} 浏览器/智能路由重任务已接互斥锁 "
                    f"@行[{_lineno('_withBrowserLock(() => _handleBrowserAgent')}] 与 @行[{_lineno('_withBrowserLock(() => _handleAuto')}]")

        ok = not missing and not busy_regressed and poll_ok and lock_wired
        trace = {"场景": "静态契约：V294 并发三件套在位、旧单飞锁未复活、轮询 2.5s、重任务接锁",
                 "执行日志": steps,
                 "判定": "并发原语全部在位，'一个任务锁死整机'不会回归 ✓" if ok else
                         "并发契约被破坏 ✗（查 desktop/main.js 的派活 runner 区）"}
        fm = "" if ok else ("原语缺失" if missing else ("单飞锁复活" if busy_regressed else "轮询/接锁回退"))
        return RunOutcome(ok, 1.0 if ok else 0.0, fm,
                          f"缺失{len(missing)}项/单飞{'回归' if busy_regressed else '无'}", trace)

    rep.add(run_case_ntimes("守卫-并发三件套在位且旧锁未复活", case_primitives, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 2. 派活队列全生命周期（真队列、临时库、含掉线自愈）
# ════════════════════════════════════════════════════════════════════════
def run_dispatch_lifecycle(k: int = 2) -> SuiteReport:
    rep = SuiteReport("桌面端·派活队列生命周期")

    def _tmp_env():
        """把队列指到独立临时目录（dispatch.db 按 HASHMM_DATA_DIR 解析），返回 (dir, 还原函数)。"""
        old_dir = os.environ.get("HASHMM_DATA_DIR")
        old_to = os.environ.get("HASHMM_DISPATCH_TIMEOUT")
        import tempfile
        d = tempfile.mkdtemp(prefix="hmm_dq_")
        os.environ["HASHMM_DATA_DIR"] = d

        def restore():
            if old_dir is None:
                os.environ.pop("HASHMM_DATA_DIR", None)
            else:
                os.environ["HASHMM_DATA_DIR"] = old_dir
            if old_to is None:
                os.environ.pop("HASHMM_DISPATCH_TIMEOUT", None)
            else:
                os.environ["HASHMM_DISPATCH_TIMEOUT"] = old_to
        return d, restore

    def case_lifecycle():
        steps: list[str] = []
        d, restore = _tmp_env()
        try:
            from hashmm.agent import dispatch as dq

            def _dump(tid_):
                t = dq.get_task(tid_) or {}
                return (f"status={t.get('status')!r} created={t.get('created') and round(t['created'], 2)} "
                        f"claimed_at={t.get('claimed_at') and round(t['claimed_at'], 2)} "
                        f"done_at={t.get('done_at') and round(t['done_at'], 2)} "
                        f"result={str(t.get('result') or '')[:40]!r}")

            tid = dq.create_task("rA", "screenshot", {"q": "截个屏"}, created_by="tester")
            _log(steps, f"建任务 {tid}（runner=rA, kind=screenshot）→ 行态[{_dump(tid)}]")
            t0 = dq.get_task(tid)
            claimed = dq.poll("rA")
            _log(steps, f"rA 认领 → 拿到 {claimed and claimed.get('task_id')}（应={tid}），payload 往返={claimed and claimed.get('payload')} → 行态[{_dump(tid)}]")
            t1 = dq.get_task(tid)
            again = dq.poll("rA")
            _log(steps, f"再次认领 → {again}（已 claimed，应拿不到=None）")
            done = dq.complete(tid, True, "截图完成")
            t2 = dq.get_task(tid)
            _log(steps, f"回填 ok → complete 返回 {done} → 行态[{_dump(tid)}]")
            dup = dq.complete(tid, False, "迟到的重复回填")
            t3 = dq.get_task(tid)
            _log(steps, f"重复回填 → 返回 {dup}（应 False 幂等拒绝）→ 行态[{_dump(tid)}]（status 不得被改成 failed、result 不得被覆盖）")
            ok = (t0 and t0.get("status") == "pending"
                  and claimed and claimed.get("task_id") == tid
                  and claimed.get("payload", {}).get("q") == "截个屏"
                  and t1 and t1.get("status") == "claimed"
                  and again is None
                  and done and t2 and t2.get("status") == "done"
                  and dup is False and t3 and t3.get("status") == "done")
            trace = {"场景": "建→认领(原子)→重复认领拒绝→回填→重复回填幂等拒绝",
                     "执行日志": steps,
                     "判定": "队列全生命周期正确（桌面派活地基稳）✓" if ok else "队列生命周期有缺陷 ✗（查 hashmm/agent/dispatch.py）"}
            return RunOutcome(bool(ok), 1.0 if ok else 0.0, "" if ok else "生命周期异常",
                              f"pending→claimed→done，幂等拒绝={dup is False}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})
        finally:
            restore()

    def case_stale_requeue():
        steps: list[str] = []
        d, restore = _tmp_env()
        try:
            os.environ["HASHMM_DISPATCH_TIMEOUT"] = "2"   # 2 秒即视为掉线
            from hashmm.agent import dispatch as dq
            tid = dq.create_task("rB", "watch", {"i": 1})
            got = dq.poll("rB")
            _log(steps, f"rB 认领 {got and got.get('task_id')} 后模拟 runner 掉线（不回填）")
            # 把 claimed_at 拨回过去（比真睡 2 秒稳），触发 _requeue_stale
            with sqlite3.connect(str(Path(d) / "dispatch.db")) as c:
                c.execute("UPDATE tasks SET claimed_at=? WHERE id=?", (time.time() - 9999, tid))
            _log(steps, "把 claimed_at 拨到 9999s 前（超过 2s 超时线）")
            re_claim = dq.poll("rB")
            _log(steps, f"再次 poll → {re_claim and re_claim.get('task_id')}（超时任务应自动回队被重新认领）")
            ok = got and got.get("task_id") == tid and re_claim and re_claim.get("task_id") == tid
            trace = {"场景": "runner 掉线（认领后不回填）→ 超时自愈回 pending → 可被重新认领",
                     "执行日志": steps,
                     "判定": "掉线自愈生效，任务不会卡死在 claimed ✓" if ok else "超时未回队 ✗（查 _requeue_stale/HASHMM_DISPATCH_TIMEOUT）"}
            return RunOutcome(bool(ok), 1.0 if ok else 0.0, "" if ok else "超时未回队",
                              f"重认领={'成功' if ok else '失败'}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})
        finally:
            restore()

    def case_runner_isolation():
        steps: list[str] = []
        d, restore = _tmp_env()
        try:
            from hashmm.agent import dispatch as dq
            tid = dq.create_task("rC", "task", {"x": 1})
            other = dq.poll("rX")
            mine = dq.poll("rC")
            _log(steps, f"rC 的任务：rX 认领 → {other}（应 None）；rC 认领 → {mine and mine.get('task_id')}（应={tid}）")
            ok = other is None and mine and mine.get("task_id") == tid
            trace = {"场景": "跨 runner 隔离：别的 runner 抢不到不属于它的任务",
                     "执行日志": steps,
                     "判定": "runner 隔离正确 ✓" if ok else "任务被别的 runner 抢走 ✗"}
            return RunOutcome(bool(ok), 1.0 if ok else 0.0, "" if ok else "隔离失效",
                              f"rX={other} rC={'命中' if ok else '未中'}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})
        finally:
            restore()

    rep.add(run_case_ntimes("派活-全生命周期+幂等回填", case_lifecycle, max(1, k)))
    rep.add(run_case_ntimes("派活-掉线超时自愈回队", case_stale_requeue, max(1, k)))
    rep.add(run_case_ntimes("派活-跨runner隔离", case_runner_isolation, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 3. 多智能体画布（结构 + 时钟 + 耗时 + 着色 + XSS 逃逸）
# ════════════════════════════════════════════════════════════════════════
def run_team_canvas(k: int = 2) -> SuiteReport:
    rep = SuiteReport("桌面端·多智能体画布")

    def case_structure():
        steps: list[str] = []
        try:
            from hashmm.agent import team as T
            roles = [{"role": "研究员", "task": "收集资料"}, {"role": "写作员", "task": "成文"}]
            hp = T._canvas_html("测试目标", roles, "parallel")
            hq = T._canvas_html("测试目标", roles, "pipeline")
            checks = {
                "并行模式有机器可读语义": "data-mode='parallel'" in hp,
                "并行含实时时钟": "tm-clock" in hp and "已运行" in hp,
                "流水线模式有机器可读语义": "data-mode='pipeline'" in hq,
                "流水线含步序#1": "#1" in hq,
                "流水线含无障碍交接标记": "pipe-arrow" in hq and "aria-label='交接到下一角色'" in hq,
                "两版均含进度计数": "tm-prog" in hp and "tm-prog" in hq,
            }
            for name, good in checks.items():
                _log(steps, f"{'✓' if good else '✗'} {name}")
            ok = all(checks.values())
            trace = {"场景": "并行/流水线两版画布结构完备", "执行日志": steps,
                     "判定": "画布结构完备 ✓" if ok else "画布缺元素 ✗（查 team._canvas_html）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "结构缺失",
                              f"{sum(checks.values())}/{len(checks)} 项", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_timing_and_paint():
        steps: list[str] = []
        try:
            from hashmm.agent import team as T
            hp = T._canvas_html("目标", [{"role": "研究员", "task": "收集"}], "parallel")
            span_ok = T._st(0, "ok", ms=3210)
            _log(steps, f"耗时标签：{span_ok!r}（应含 3.2s）")
            painted = re.sub(r"<span class='st st-\w+' id='tm-0'[^>]*>.*?</span>", span_ok, hp, count=1)
            hit = "已完成 · 3.2s" in painted and "data-state='ok'" in painted
            _log(steps, f"{'✓' if hit else '✗'} 着色正则命中并把耗时写上画布")
            fail_span = T._st(0, "fail", "空输出", ms=980)
            fail_ok = (
                "失败 · 1.0s" in fail_span
                and "data-state='fail'" in fail_span
                and "空输出" in fail_span
            )
            _log(steps, f"{'✓' if fail_ok else '✗'} 失败态含耗时与原因 tooltip：{fail_span!r}")
            ok = "3.2s" in span_ok and hit and fail_ok
            trace = {"场景": "角色耗时上标签 + _mark 着色正则可命中（成功/失败两态）",
                     "执行日志": steps,
                     "判定": "耗时/着色链路正确 ✓" if ok else "耗时或着色断了 ✗（查 team._st/_mark 正则）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "着色/耗时断链",
                              f"ok标签含耗时={'3.2s' in span_ok} 命中={hit}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_xss_escape():
        steps: list[str] = []
        try:
            from hashmm.agent import team as T
            evil_goal = "<script>alert(1)</script>"
            evil_role = [{"role": "<img src=x onerror=alert(2)>", "task": "正常任务 & <b>加粗</b>"}]
            h = T._canvas_html(evil_goal, evil_role, "parallel")
            raw_script = "<script>alert(1)</script>" in h
            raw_img = "<img src=x onerror" in h
            escaped = ("&lt;script&gt;" in h) and ("&lt;img" in h)
            _log(steps, f"目标含 <script> → 原样出现={raw_script}（必须 False），已转义={('&lt;script&gt;' in h)}")
            _log(steps, f"角色名含 <img onerror> → 原样出现={raw_img}（必须 False）")
            ok = (not raw_script) and (not raw_img) and escaped
            trace = {"场景": "画布是可发布共享的 HTML——目标/角色名注入 <script>/<img onerror> 必须被转义",
                     "执行日志": steps,
                     "判定": "XSS 逃逸正确（发布页安全）✓" if ok else "存在 XSS 注入面 ✗（查 team._esc 是否覆盖所有插值点）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "XSS注入面",
                              f"script原样={raw_script} img原样={raw_img}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("画布-并行/流水线结构完备", case_structure, max(1, k)))
    rep.add(run_case_ntimes("画布-角色耗时+着色链路", case_timing_and_paint, max(1, k)))
    rep.add(run_case_ntimes("画布-XSS注入必须被转义", case_xss_escape, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 4. /activity 数据契约（客户端任务进度：streaming 出现 / complete 消失 / 用户隔离）
# ════════════════════════════════════════════════════════════════════════
def run_activity_contract(k: int = 2) -> SuiteReport:
    rep = SuiteReport("桌面端·活动接口契约")

    def case_contract():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            # u1 两条 streaming（不同会话），u2 一条 streaming
            c1 = "conv_" + uuid.uuid4().hex[:8]
            c2 = "conv_" + uuid.uuid4().hex[:8]
            c3 = "conv_" + uuid.uuid4().hex[:8]
            db.create_conversation(c1, "u1", "任务一")
            db.create_conversation(c2, "u1", "任务二")
            db.create_conversation(c3, "u2", "别人的任务")
            m1 = db.create_message(c1, "assistant", "生成中…", status="streaming")
            time.sleep(0.02)
            db.create_message(c2, "assistant", "也在生成…", status="streaming")
            db.create_message(c3, "assistant", "隔壁在生成…", status="streaming")
            _log(steps, "u1 两条 streaming（先 c1 后 c2）+ u2 一条 streaming")
            act = db.get_active_chats("u1")
            ids = [a.get("conv_id") for a in act]
            for a in act:
                _log(steps, f"  原始行：conv={a.get('conv_id')} title={a.get('title')!r} msg={a.get('msg_id')} created={a.get('created_at') and round(a['created_at'], 2)}")
            _log(steps, f"get_active_chats(u1) → {ids}（应含 c1、c2，最新在前=c2 先）")
            in_ok = set(ids) == {c1, c2}
            order_ok = ids and ids[0] == c2
            titled = all(a.get("title") for a in act)
            isolated = c3 not in ids
            _log(steps, f"包含正确={in_ok} 最新在前={order_ok} 带标题={titled} 用户隔离={isolated}")
            # 完成 c1 → 应从活动里消失
            db.update_message(m1, content="完成了", status="complete")
            act2 = [a.get("conv_id") for a in db.get_active_chats("u1")]
            gone = c1 not in act2 and c2 in act2
            _log(steps, f"c1 收尾 complete → 活动列表 {act2}（c1 应消失、c2 仍在）={gone}")
            ok = in_ok and order_ok and titled and isolated and gone
            trace = {"场景": "streaming 出现/complete 消失/最新在前/带标题/跨用户隔离",
                     "执行日志": steps,
                     "判定": "活动契约全部满足（App 任务进度与桌面任务可见性同源可信）✓" if ok
                             else "活动契约破损 ✗（查 database.get_active_chats）"}
            fm = "" if ok else ("用户隔离失效" if not isolated else ("完成未消失" if not gone else "包含/排序错误"))
            return RunOutcome(ok, 1.0 if ok else 0.0, fm,
                              f"含{in_ok}/序{order_ok}/隔离{isolated}/消失{gone}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("活动-出现/消失/排序/隔离一体契约", case_contract, max(1, k)))
    return rep

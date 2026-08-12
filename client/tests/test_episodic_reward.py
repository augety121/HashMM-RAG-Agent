"""EpisodicMemory 奖励驱动升级单测——注入内存 sqlite，沙箱直跑。"""
import sys, sqlite3, contextlib, time
import os as _os; sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
from hashmm.evolution.episodic_memory import (
    EpisodicMemory, MARK_GOOD, MARK_BAD, REWARD_GOOD_MIN, REWARD_BAD_MAX,
)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}  <<< FAIL")


class MemDB:
    """模拟 hashmm.api.database 的 _conn() 上下文管理器接口。"""
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
    @contextlib.contextmanager
    def _conn(self):
        yield self.conn
        self.conn.commit()


def fresh():
    return EpisodicMemory(db_module=MemDB())


print("=== 1. compute_reward 纯函数 ===")
cr = EpisodicMemory.compute_reward
check("up 反馈→高 reward", cr(feedback="up") > 0.85)
check("down 反馈→低 reward", cr(feedback="down") < 0.25)
check("无反馈→中性附近", 0.3 < cr() < 0.7)
check("高接地率拉高", cr(faithfulness_ratio=1.0) > cr(faithfulness_ratio=0.0))
check("高置信拉高", cr(confidence=0.95) > cr(confidence=0.1))
check("含错误措辞降低", cr(answer="抱歉，无法回答这个问题") < 0.5)
check("裁剪到[0,1]", 0.0 <= cr(feedback="down", faithfulness_ratio=0.0, answer="出错") <= 1.0)
check("接地率主导(高)", cr(feedback="", faithfulness_ratio=0.95, n_sources=5, top_score=2.0) > 0.65)

print("\n=== 2. 迁移：旧表(无 reward 列)幂等补列 ===")
db = MemDB()
# 先建一个"旧版"表（没有 reward 列），模拟真机已有数据
with db._conn() as c:
    c.execute("""CREATE TABLE episodes (id TEXT PRIMARY KEY, user_id TEXT, query TEXT NOT NULL,
        query_type TEXT, strategy TEXT, outcome TEXT, feedback TEXT, key_entities TEXT,
        key_insight TEXT, answer_length INTEGER, elapsed_ms INTEGER, created_at REAL)""")
    c.execute("INSERT INTO episodes(id,user_id,query,created_at) VALUES('old1','u','历史问题',?)", (time.time(),))
mem = EpisodicMemory(db_module=db)
mem._ensure_db()   # 应触发 ALTER 补 reward 列，且不丢旧数据
with db._conn() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(episodes)").fetchall()}
    cnt = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("旧表补出 reward 列", "reward" in cols)
check("旧数据未丢失", cnt == 1)
check("迁移幂等(再调不报错)", (mem._ensure_db() or True))

print("\n=== 3. record 修复：之前报 TypeError 的 kwarg 现在能写入 ===")
mem = fresh()
# 这正是 streaming._run_evolution_safe 的真实调用形态（曾经整个抛 TypeError）
mem.record(episode_id="e1", user_id="u", query="A公司2024营收对比", query_type="compare",
           strategy="grounded", retrieval_mode="mix", n_sources=3, top_score=2.1,
           answer_length=300, elapsed_ms=80, faithfulness_ratio=0.95)
with mem._db._conn() as c:
    rows = c.execute("SELECT * FROM episodes").fetchall()
check("episode 真正写入(不再静默失败)", len(rows) == 1)
check("reward 已落地且高(高接地率)", float(dict(rows[0])["reward"]) > 0.6)
check("answer_length 落地", dict(rows[0])["answer_length"] == 300)

print("\n=== 4. 显式 reward 优先于自动计算 ===")
mem.record(user_id="u", query="X", reward=0.33)
with mem._db._conn() as c:
    r = c.execute("SELECT reward FROM episodes WHERE query='X'").fetchone()
check("显式 reward 被采用", abs(float(r[0]) - 0.33) < 1e-6)

print("\n=== 5. 经验质量门(env 下限) ===")
import os
os.environ["HASHMM_EPISODE_MIN_REWARD"] = "0.5"
mem2 = fresh()
mem2.record(user_id="u", query="低质量回答", reward=0.2)   # 低于下限→不入库
mem2.record(user_id="u", query="高质量回答", reward=0.9)   # 高于下限→入库
with mem2._db._conn() as c:
    qs = [dict(r)["query"] for r in c.execute("SELECT query FROM episodes").fetchall()]
check("低于下限被挡", "低质量回答" not in qs)
check("高于下限入库", "高质量回答" in qs)
os.environ.pop("HASHMM_EPISODE_MIN_REWARD")

print("\n=== 6. recall 奖励加权：高 reward 同等相关时排前 ===")
mem = fresh()
# 两条对同一查询同等词法相关，但 reward 不同
mem.record(user_id="u", query="比亚迪 营收 2024", strategy="bad_strat", reward=0.15)
mem.record(user_id="u", query="比亚迪 营收 2024", strategy="good_strat", reward=0.95)
res = mem.recall("u", "比亚迪 营收 2024", limit=2)
check("召回到 2 条", len(res) == 2)
check("高 reward 排第一", res[0]["strategy"] == "good_strat")
check("relevance 含 reward 贡献", res[0]["relevance_score"] > res[1]["relevance_score"])

print("\n=== 7. get_strategy_hint 奖励感知(好经验/前车之鉴) ===")
mem = fresh()
mem.record(user_id="u", query="对比 营收 增长", strategy="多跳检索",
           reward=REWARD_GOOD_MIN + 0.2)   # 稳超好评阈值
hint_good = mem.get_strategy_hint("u", "对比 营收 增长")
check(f"高 reward→{MARK_GOOD} 标记", MARK_GOOD in hint_good)
mem2 = fresh()
mem2.record(user_id="u", query="对比 利润 趋势", strategy="单跳直答",
            reward=REWARD_BAD_MAX - 0.15)   # 稳低于待改进阈值（且 > 0）
hint_bad = mem2.get_strategy_hint("u", "对比 利润 趋势")
check(f"低 reward→{MARK_BAD} 标记", MARK_BAD in hint_bad)

print("\n=== 8. update_reward 可移植(不依赖 UPDATE ORDER BY LIMIT) ===")
mem = fresh()
mem.record(episode_id="upd1", user_id="u", query="待回填", reward=0.5)
mem.update_reward("u", 0.88)   # 更新最近一条
with mem._db._conn() as c:
    r = c.execute("SELECT reward FROM episodes WHERE id='upd1'").fetchone()
check("最近一条 reward 被更新", abs(float(r[0]) - 0.88) < 1e-6)
mem.update_reward("u", 0.11, episode_id="upd1")
with mem._db._conn() as c:
    r = c.execute("SELECT reward FROM episodes WHERE id='upd1'").fetchone()
check("按 id 更新", abs(float(r[0]) - 0.11) < 1e-6)

print("\n=== 9. decay 奖励感知：优先淘汰低 reward ===")
mem = fresh()
mem._ensure_db()   # 先建表再直接插数据
now = time.time()
# 直接插入不同 reward 的多条，超 max_entries 时应保留高 reward
with mem._db._conn() as c:
    for i in range(10):
        c.execute("""INSERT INTO episodes(id,user_id,query,feedback,reward,created_at)
                     VALUES(?,?,?,?,?,?)""",
                  (f"d{i}", "u", f"q{i}", "", i / 10.0, now - i))  # reward 0.0~0.9
mem.decay(max_age_days=3650, max_entries=5)   # 只留 5 条，应是高 reward 的
with mem._db._conn() as c:
    kept = sorted(float(dict(r)["reward"]) for r in c.execute("SELECT reward FROM episodes").fetchall())
check("超额裁剪后剩 5 条", len(kept) == 5)
check("保留的是高 reward(最低留存≥0.5)", min(kept) >= 0.5)

print("\n=== 10. snapshot / rollback 安全阀 ===")
mem = fresh()
mem.record(user_id="u", query="good_state_1", reward=0.8)
mem.record(user_id="u", query="good_state_2", reward=0.7)
n_snap = mem.snapshot()
check("快照保存 2 条", n_snap == 2)
# 模拟"学到错经验"污染
mem.record(user_id="u", query="bad_learned", reward=0.05)
with mem._db._conn() as c:
    before = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("污染后有 3 条", before == 3)
n_roll = mem.rollback()
with mem._db._conn() as c:
    qs = [dict(r)["query"] for r in c.execute("SELECT query FROM episodes").fetchall()]
check("回滚到 2 条", n_roll == 2)
check("坏经验被回滚掉", "bad_learned" not in qs)
check("好经验仍在", "good_state_1" in qs and "good_state_2" in qs)

print("\n=== 11. 向后兼容：老式简单 record 调用仍可用 ===")
mem = fresh()
mem.record("u", "简单调用", "compare", "grounded", "success", "up", "一段答案", 50)
with mem._db._conn() as c:
    r = c.execute("SELECT * FROM episodes").fetchone()
check("旧式位置参数调用正常", dict(r)["query"] == "简单调用")
check("up 反馈→自动算出高 reward", float(dict(r)["reward"]) > 0.8)

print(f"\n{'='*48}\n结果：PASS={PASS}  FAIL={FAIL}")

# ── V308 修 P0-6（测试假绿）──────────────────────────────────────────
# 原先此处是【顶层裸 sys.exit()】。测试文件被 import 时（pytest 收集阶段、
# 自制 runner 的 exec_module）会立即抛 SystemExit 杀死宿主进程：
#   · 真 pytest → INTERNALERROR: mainloop: caught unexpected SystemExit
#   · _mini_runner → SystemExit 不是 Exception 子类，except Exception 抓不到，
#     整个套件以退出码 0 提前终止 → 后续测试文件从未运行却报“全绿”。
# 且本文件的 ck()/ok() 只累加计数、【不抛异常】，故即使不崩，pytest 也只会
# 报 "no tests ran"——检查结果永远变不成测试结论。
# 修法：补一个真正的 pytest 入口断言（读取 import 期已算好的失败计数），
# 并把 sys.exit 收进 __main__ 保护，保留 `python tests/test_episodic_reward.py` 直跑的能力。
def test_all():
    """pytest 入口：任一检查失败即断言失败（不再依赖 sys.exit 传递结果）。"""
    assert FAIL == 0, f"{FAIL} 项检查未通过（详见上方 ✗ 行）"


if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)

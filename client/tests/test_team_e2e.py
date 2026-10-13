"""tests/test_team_e2e.py — 多智能体团队端到端测试（V306）。

用 mock LLM 驱动真实的团队编排（_decompose / _run_role / _synthesize / start_team 的 _run_all），
覆盖 parallel 与 pipeline 两种模式 + 角色失败注入，验证：
  · 拆解：坏 JSON → 诚实降级到固定三人组；
  · parallel：多角色并行、结果汇总；
  · pipeline：后一棒拿到前序产出（本套件**已抓到并修复** quick_call 丢 prior 的 bug）；
  · 角色失败注入：单角色失败不拖垮团队，final 仍从存活角色合成，失败被如实记账；
  · 全失败：无 findings 时不产出假汇总。

沙箱可直接跑（真实 team 模块 + asyncio）。
"""
import asyncio
import importlib
import json
import os
import re
import sys as _sys, os as _os
import tempfile
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
_os.environ.setdefault("HASHMM_DATA_DIR", tempfile.mkdtemp())

from hashmm.agent import team as T


class MockTeamLLM:
    """按团队各调用点作答的 mock；可注入指定角色失败、记录 pipeline prior 传递。"""

    def __init__(self, roles, fail_roles=None, bad_decompose=False):
        self.roles = roles
        self.fail_roles = set(fail_roles or [])
        self.bad_decompose = bad_decompose
        self.prior_seen = {}   # role -> 是否在其输入里看到前序产出
        self.model = "mock"

    def _role_of(self, text):
        m = re.search(r"「(.+?)」", text or "")
        return m.group(1) if m else ""

    def __call__(self, prompt):
        if "拆成" in prompt or "协调者" in prompt:
            if self.bad_decompose:
                return "这不是合法 JSON，拆解失败"
            return json.dumps([{"role": r, "task": f"{r}的分工"} for r in self.roles], ensure_ascii=False)
        if "汇总者" in prompt:
            return "【一句话总结】团队完成。\n- 要点A\n- 要点B"
        # 角色 run_llm 回退路径
        role = self._role_of(prompt)
        if role in self.fail_roles:
            return ""
        if "前序同事已产出" in prompt:
            self.prior_seen[role] = True
        return f"{role}的产出：已完成。"

    def quick_call(self, sys_p, user_p, max_tok=None):
        if "汇总者" in sys_p:
            return "【一句话总结】团队完成目标。\n- 要点A\n- 要点B"
        role = self._role_of(sys_p)
        if role in self.fail_roles:
            return ""   # 触发回退到 run_llm，run_llm 对失败角色也返回空 → 该角色失败
        if "前序同事已产出" in user_p:
            self.prior_seen[role] = True
        return f"{role}的产出：已完成分工。"


def _patch_llm(mock):
    """把 get_active_llm_fn 换成返回 mock。"""
    from hashmm.api import model_manager
    model_manager.get_active_llm_fn = lambda: (mock, "mock")


async def _drive_team(goal, mock, mode, roles_override=None):
    """启动团队并等后台 _run_all 跑完，返回最终 team 状态。"""
    _patch_llm(mock)
    user = {"uid": "u1", "sub": "tester"}
    res = await T.start_team(user, goal, conv_id="", roles_override=roles_override, mode=mode)
    team_id = res["team_id"]
    # 等后台任务把状态推进到终态（done/failed）
    for _ in range(200):
        await asyncio.sleep(0.05)
        t = T.get_team(team_id)
        if t and t.get("status") in ("done", "failed"):
            return t
    return T.get_team(team_id)


# ══════════════════════════════ 拆解 ══════════════════════════════
def test_decompose_parses_roles():
    fn = MockTeamLLM(["调研", "分析", "成文"])
    roles = T._decompose(fn, "写一份市场报告")
    assert len(roles) == 3 and roles[0]["role"] == "调研"


def test_decompose_bad_json_falls_back():
    fn = MockTeamLLM(["x"], bad_decompose=True)
    roles = T._decompose(fn, "目标")
    assert len(roles) >= 2, "坏 JSON 应诚实降级到固定角色组"


# ══════════════════════════════ pipeline 传 prior（修复验证）══════════════════════════════
def test_run_role_pipeline_passes_prior():
    fn = MockTeamLLM(["分析"])
    T._run_role(fn, "目标", {"role": "分析", "task": "分析"}, prior="【调研】前序结论ABC", uid="")
    assert fn.prior_seen.get("分析") is True, "pipeline 模式 quick_call 未收到 prior（回归）"


def test_run_role_parallel_no_prior():
    fn = MockTeamLLM(["调研"])
    out = T._run_role(fn, "目标", {"role": "调研", "task": "查"}, prior="", uid="")
    assert out and "调研" in out and not fn.prior_seen, "parallel 不应有 prior"


# ══════════════════════════════ 端到端：两种模式 ══════════════════════════════
def test_e2e_parallel_all_succeed():
    fn = MockTeamLLM(["调研", "分析", "成文"])
    t = asyncio.run(_drive_team("并行做一份报告", fn, "parallel"))
    assert t["status"] == "done", f"并行团队未完成：{t.get('status')}"
    assert t.get("ok_n") == 3 and t.get("total") == 3, f"角色成功数不对：{t.get('ok_n')}/{t.get('total')}"
    assert t.get("final"), "应产出汇总"


def test_e2e_pipeline_all_succeed_and_prior_flows():
    fn = MockTeamLLM(["调研", "分析", "成文"])
    t = asyncio.run(_drive_team("流水线做一份报告", fn, "pipeline"))
    assert t["status"] == "done", f"流水线团队未完成：{t.get('status')}"
    assert t.get("ok_n") == 3, f"角色成功数不对：{t.get('ok_n')}"
    # pipeline：第 2、3 棒应看到前序产出
    assert fn.prior_seen.get("分析") is True or fn.prior_seen.get("成文") is True, \
        "流水线后续角色未拿到前序产出"


# ══════════════════════════════ 角色失败注入 ══════════════════════════════
def test_e2e_one_role_fails_team_survives():
    fn = MockTeamLLM(["调研", "分析", "成文"], fail_roles={"分析"})
    t = asyncio.run(_drive_team("一个角色会失败的任务", fn, "parallel"))
    assert t["status"] == "done", f"单角色失败不应拖垮团队：{t.get('status')}"
    assert t.get("ok_n") == 2 and t.get("total") == 3, f"应 2/3 成功：{t.get('ok_n')}/{t.get('total')}"
    assert t.get("final"), "存活角色应仍能合成汇总"
    # 失败角色状态应记为 fail
    roles = {r["role"]: r["state"] for r in t.get("roles", [])}
    assert roles.get("分析") == "fail", f"失败角色未记 fail：{roles}"


def test_e2e_all_roles_fail_no_fake_summary():
    fn = MockTeamLLM(["调研", "分析"], fail_roles={"调研", "分析"})
    t = asyncio.run(_drive_team("全员失败的任务", fn, "parallel"))
    assert t["status"] == "failed", f"全失败应标记 failed：{t.get('status')}"
    assert not t.get("final"), "全失败不应产出假汇总"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("多智能体团队 E2E（V306：parallel/pipeline + 角色失败注入）")
    print("=" * 60)
    import time
    p = f = 0
    t0 = time.time()
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"结果：PASS={p}  FAIL={f}  用时 {time.time() - t0:.1f}s")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())

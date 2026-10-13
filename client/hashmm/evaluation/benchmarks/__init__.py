"""HashMM 外部基准对标套件（V306）。

把业界标准的 agent 评测基准（SWE-bench Verified / Terminal-bench / 工具调用 / τ²-bench 等）
接进 HashMM 的测试中枢，让你在中枢界面上【勾选即可跑】，并与顶尖 agent 的公开分数对比。

设计要点：
  · 每个基准都能【优雅降级】——AutoDL 上装了对应基准(Docker/仓库)就跑真集，没装就返回 SKIP
    并给出安装指引(install.sh)，绝不因缺依赖而崩。
  · 每个基准都有【smoke 模式】——用内置极小任务集离线跑通整条适配管线(不需要 Docker)，
    用于自检"接线是否正确"，也是本仓在无外部依赖环境下能严格验证的部分。
  · 结果统一携带 leaderboard 对比(你的分 vs Claude Code / 顶尖 agent 的公开分)。
"""
from .registry import BENCHMARKS, get_benchmark
from .runner import comparable_candidates, run_benchmark, run_for_comparison

__all__ = ["BENCHMARKS", "get_benchmark", "run_benchmark",
           "comparable_candidates", "run_for_comparison"]

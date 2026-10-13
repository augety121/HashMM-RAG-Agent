#!/usr/bin/env python3
"""remote_bench_runner.py —— 在【免费 CI（有 Docker）】上跑 Docker 基准，结果回传你的后端。

解决"你只有服务器、没有 Docker，跑不了 SWE-bench/WebArena/OSWorld/Terminal"的问题：
  · 在 GitHub Actions（每月 2000 分钟免费、原生 Docker）上跑这些基准；
  · LLM 推理走【你自己后端的公网地址】（模型还是你的，不额外花钱）；
  · 跑完把分数 POST 到你后端的 /api/selftest/bench/ingest → 自动进「和大厂对比」和对比图。

用法（在 CI 或任意有 Docker 的机器上）：
    export HASHMM_BACKEND_URL=http://你的公网IP:20014          # 你后端地址（回传结果 + 取模型）
    export HASHMM_BENCH_INGEST_TOKEN=<和后端同一个 token>       # 鉴权，防伪造
    export HASHMM_OPENAI_BASE=http://你的模型OpenAI兼容地址/v1  # 模型推理入口（vLLM/你的后端）
    export HASHMM_OPENAI_KEY=sk-xxx                            # 没有就填 EMPTY
    export HASHMM_OPENAI_MODEL=Qwen2.5-7B-Instruct             # 模型名
    python scripts/remote_bench_runner.py --bench swebench --sample standard

    # 一次跑多个：--bench swebench terminal
    # 不回传只本地看：--no-ingest

依赖：完整 hashmm 源码树 + 目标基准的数据集（CI 里先 bash install.sh <bench>）+ Docker。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _configure_console_streams() -> None:
    """Avoid console-encoding crashes (for example, Windows GBK + emoji)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


_configure_console_streams()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# CI 上能跑、且需要 Docker 才有官方口径的基准（你服务器跑不了的那些）
# ★ V332：把服务器上原本免 Docker 就能跑的 6 个也接进 CI——GitHub Actions 同样能跑，
#   一次 workflow 就能出全套分数（此前 README 宣传了 GAIA/HumanEval/Kotlin/BFCL/τ²，
#   但 runner 实际不认这些短名，传了会被「不在 CI 基准清单」跳过——本轮补齐）。
_CI_BENCHES = {"swebench", "terminal", "webarena", "osworld", "swebench_pro",
               "gaia", "tau2", "humaneval", "bfcl", "kotlin", "webvoyager"}
# 别名（与 install.sh 同词汇）：nodocker=免 Docker 六项；all=全部 11 项。
_ALIASES = {
    "nodocker": ["humaneval", "bfcl", "tau2", "gaia", "kotlin", "webvoyager"],
    "all": ["swebench", "terminal", "swebench_pro", "webarena", "osworld",
            "humaneval", "bfcl", "tau2", "gaia", "kotlin", "webvoyager"],
}
# 这两项除了任务文件还依赖用户自备的网站/桌面 VM。没配外部环境是正常 SKIP，
# 应像 pytest skip 一样保留诊断和 Artifact，但不能把整个 Actions 工作流标红。
_OPTIONAL_EXTERNAL_ENV = {"webarena", "osworld"}
# ★ V332：GAIA/WebVoyager 靠联网搜索出分。没配搜索 key 跑了也是白烧 token（几乎全错），
#   所以视为「缺可选外部环境」——落盘 skip 原因、不标红工作流，并给出确切的 Secret 名。
_NEEDS_SEARCH_KEY = {"gaia", "webvoyager"}
_SEARCH_KEY_ENVS = ("HASHMM_SERPER_API_KEY", "HASHMM_TAVILY_API_KEY", "HASHMM_BING_API_KEY")
# ★ V326 修真 bug：CLI 短名 → registry 里的真实 bench_id。
#   此前把 swebench→"swebench"、terminal→"terminal"，但注册表里实际是
#   "swebench_verified" / "terminal_bench"，导致 run_benchmark() 返回「未知基准」直接跳过——
#   而工作流默认输入就是 swebench，即最常见的一次 CI 运行完全跑不出分。已用注册表真实数据验证。
_BENCH_ID_MAP = {
    "swebench": "swebench_verified", "terminal": "terminal_bench", "webarena": "webarena",
    "osworld": "osworld", "swebench_pro": "swebench_pro",
    "gaia": "gaia", "tau2": "tau2_bench", "humaneval": "humaneval",
    "bfcl": "tool_calling", "kotlin": "kotlin_bench", "webvoyager": "webvoyager",
}


def expand_benches(benches: list[str]) -> list[str]:
    """展开别名并去重保序（workflow 与本脚本共用同一词汇，防两处漂移）。"""
    out: list[str] = []
    for b in benches:
        for x in _ALIASES.get(b, [b]):
            if x not in out:
                out.append(x)
    return out


def _search_key_present() -> bool:
    return any((os.environ.get(k) or "").strip() for k in _SEARCH_KEY_ENVS)


def _make_llm_fn():
    """构造 AgentLoop 可用的 OpenAI-compatible 工具调用适配器。"""
    base = (os.environ.get("HASHMM_OPENAI_BASE") or "").rstrip("/")
    key = os.environ.get("HASHMM_OPENAI_KEY") or "EMPTY"
    model = os.environ.get("HASHMM_OPENAI_MODEL") or "default"
    if not base:
        return None

    try:
        from hashmm.api.model_manager import make_llm_fn_from_model
        llm = make_llm_fn_from_model({
            # 通用 OpenAI-compatible 路径；允许本地端点使用 EMPTY key，且不会重写显式 URL/model。
            "provider": "vllm",
            "api_key": key,
            "base_url": base,
            "model_name": model,
            "temperature": float(os.environ.get("HASHMM_OPENAI_TEMPERATURE", "0")),
            "max_tokens": int(os.environ.get("HASHMM_OPENAI_MAX_TOKENS", "8192")),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 模型适配器初始化失败：{type(exc).__name__}: {exc}")
        return None
    # SWE-bench 必须真正调用 read_file/str_replace/run_shell 等工具。普通文本 callable 会被
    # AgentLoop 判为“LLM 未就绪”，最终产生 status=PASS 但补丁为空的假成功。
    if llm is None or not callable(getattr(llm, "call_with_tools", None)):
        print("ERROR: 模型适配器不支持 call_with_tools，不能运行 Agent 基准")
        return None
    return llm


def _run_one(bench: str, bench_id: str, sample: str, llm,
             baseline: bool) -> tuple[str, str, dict, int]:
    """在线程中运行一个基准；样本档位是 thread-local，必须在工作线程内设置。"""
    from hashmm.evaluation.benchmarks import run_benchmark
    from hashmm.evaluation.benchmarks.adapter import baseline_scope
    from hashmm.evaluation.benchmarks.sample_stats import clear_forced_sample, set_forced_sample

    set_forced_sample(sample)
    try:
        t0 = time.time()
        with baseline_scope(baseline):
            result = run_benchmark(bench_id, mode="full", llm_fn=llm)
        return bench, bench_id, result, int(time.time() - t0)
    finally:
        clear_forced_sample()


def _ingest(result: dict) -> None:
    """把结果 POST 到后端 /bench/ingest。"""
    backend = (os.environ.get("HASHMM_BACKEND_URL") or "").rstrip("/")
    token = os.environ.get("HASHMM_BENCH_INGEST_TOKEN") or ""
    if not backend or not token:
        print("  ⚠️ 未设 HASHMM_BACKEND_URL / HASHMM_BENCH_INGEST_TOKEN，跳过回传。")
        return
    payload = json.dumps({
        "bench_id": result.get("id"), "name": result.get("name"),
        "score_pct": result.get("score_pct"), "passed": result.get("passed"),
        "total": result.get("total"), "kind": result.get("kind", "official"),
        "mode": "full", "elapsed_ms": result.get("elapsed_ms", 0),
        "comparable": bool(result.get("comparable")),
        "baseline": bool(result.get("baseline")),
        "breakdown": result.get("breakdown") or {}, "detail": result.get("detail", ""),
        "source": os.environ.get("HASHMM_CI_NAME", "github_actions"),
    }).encode()
    req = urllib.request.Request(backend + "/api/selftest/bench/ingest", data=payload,
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            resp = json.loads(r.read().decode("utf-8", "replace"))
        print(f"  ↑ 回传后端：{resp.get('detail', resp)}")
    except urllib.error.HTTPError as e:
        print(f"  ✗ 回传失败 HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 回传异常：{type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="在有 Docker 的机器上跑基准并回传后端")
    ap.add_argument("--bench", nargs="+", required=True,
                    help=f"要跑的基准（{', '.join(sorted(_CI_BENCHES))}；别名 nodocker=免Docker六项 / all=全部）")
    ap.add_argument("--sample", default="standard", choices=["quick", "standard", "full"])
    ap.add_argument("--no-ingest", action="store_true", help="只本地跑，不回传")
    ap.add_argument("--baseline", action="store_true",
                    help="消融基线：裸模型直答（不用 agent），与正常分对照看脚手架增益")
    ap.add_argument("--paired-baseline", action="store_true",
                    help="成对消融：同一批题依次跑裸模型基线和 Agent，产出两份可归因结果")
    ap.add_argument("--out-dir", default="bench_results",
                    help="结果 JSON 落盘目录（CI 会作为 Artifact 上传，服务器在内网也能导入）")
    ap.add_argument("--bench-workers", type=int,
                    default=int(os.environ.get("HASHMM_CI_BENCH_WORKERS", "2")),
                    help="同时运行的基准数（默认 2，安全上限 6；总LLM压力≈该值×HASHMM_BENCH_CONCURRENCY）")
    args = ap.parse_args()

    if args.baseline and args.paired_baseline:
        ap.error("--baseline 与 --paired-baseline 不能同时使用")
    # Compatibility note: the adapter still understands HASHMM_BENCH_BASELINE for
    # older automation, while this runner uses baseline_scope to avoid global leaks.
    phase_modes = [True, False] if args.paired_baseline else [bool(args.baseline)]
    if args.paired_baseline:
        print("🧪 成对消融模式：先跑裸模型，再跑你的 Agent；两轮题集/模型/档位一致")
    elif args.baseline:
        print("🧪 基线模式：裸模型直答（未用你的 agent）——结果仅作消融对照，不进正式对比区")

    rc = 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 各 benchmark 将官方 predictions、逐题失败原因和精简命令轨迹一并放进同一 Artifact。
    # 之前只留最终分数，Terminal 0 分或 SWE 补丁未通过时无法离线审计。
    os.environ["HASHMM_BENCH_ARTIFACT_DIR"] = str(out_dir.resolve())
    saved: list[str] = []
    jobs: list[tuple[str, str]] = []
    seen_benches: set[str] = set()
    for b in expand_benches(args.bench):
        if b not in _CI_BENCHES:
            print(f"⏭️  {b} 不在 CI 基准清单（{sorted(_CI_BENCHES)}；别名 {sorted(_ALIASES)}），跳过")
            continue
        if b in seen_benches:
            print(f"⏭️  {b} 重复指定，只运行一次")
            continue
        seen_benches.add(b)
        # ★ V332：GAIA/WebVoyager 没有搜索 key 时不进任务队列——合成 skip 结果落盘
        #   （Artifact 里能看到原因），像 webarena/osworld 一样不标红工作流。
        if b in _NEEDS_SEARCH_KEY and not _search_key_present():
            bid = _BENCH_ID_MAP[b]
            res = {
                "id": bid, "name": b, "kind": "official", "mode": "full",
                "skip": True, "score_pct": None,
                "detail": ("缺联网搜索 key：GAIA/WebVoyager 需要 web_search 出分。"
                           "去仓库 Settings → Secrets and variables → Actions 加 "
                           "HASHMM_SERPER_API_KEY（serper.dev 免费注册；"
                           "或 HASHMM_TAVILY_API_KEY / HASHMM_BING_API_KEY 任一）后重跑。"),
            }
            for is_baseline in phase_modes:
                phase_res = {**res, "baseline": is_baseline, "comparable": False}
                fp = out_dir / f"{bid}{'_baseline' if is_baseline else ''}.json"
                fp.write_text(json.dumps(phase_res, ensure_ascii=False, indent=2), encoding="utf-8")
                saved.append(str(fp))
            print(f"⏭️  {b}：{res['detail']}")
            print("     ℹ️ 这是缺少可选外部环境（搜索 key）的正常 SKIP，不标记工作流失败")
            continue
        jobs.append((b, _BENCH_ID_MAP[b]))
    if not jobs and not saved:
        print("❌ 没有可运行的基准")
        return 1
    if not jobs:
        print("ℹ️ 全部请求项都因缺可选外部环境被跳过；skip 原因已落盘为 Artifact。")
        return 0

    # 先展开参数、检查可选环境并创建 Artifact 目录，再初始化模型。即使模型
    # 配置或依赖损坏，CI 也必须留下结构化诊断，不能只在控制台报错后空手退出。
    llm = _make_llm_fn()
    if llm is None:
        detail = "模型配置或工具适配器不可用，无法驱动 agent；请检查 CI 模型配置与 openai 依赖。"
        print(f"ERROR: {detail}")
        print("  · 在 GitHub Actions 跑：去仓库 Settings → Secrets and variables → Actions 配模型 Secret：")
        print("    HASHMM_OPENAI_BASE / HASHMM_OPENAI_KEY / HASHMM_OPENAI_MODEL")
        print("  · 需要回传时再配 HASHMM_BACKEND_URL / HASHMM_BENCH_INGEST_TOKEN；只下载 Artifact 时可不配。")
        print("  · 模型是云端 API（如 DeepSeek）：BASE 填其 OpenAI 兼容地址（https://api.deepseek.com/v1），")
        print("    KEY 填 sk-…，MODEL 填真实模型名——CI 直连云端，不用打通你服务器的模型端口。")
        print("  · 模型是本地 vLLM：BASE 填公网映射的 http://IP:端口/v1。")
        for b, bid in jobs:
            for is_baseline in phase_modes:
                res = {
                    "id": bid, "name": b, "kind": "official", "mode": "full",
                    "skip": True, "score_pct": None, "pipeline_ok": False,
                    "baseline": is_baseline, "comparable": False, "detail": detail,
                }
                fp = out_dir / f"{bid}{'_baseline' if is_baseline else ''}.json"
                fp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
                saved.append(str(fp))
        print(f"📁 失败诊断已落盘 {len(saved)} 份：{', '.join(saved)}")
        return 1

    # ★ V332：上限 4→6（11 个基准可同跑；付费云 API 是 IO 型，瓶颈在对端而非本机 CPU。
    #   humaneval/kotlin 会在 runner 本机真跑代码/编译，故仍设硬上限防打挂 4C runner）。
    requested_workers = max(1, min(int(args.bench_workers), 6))
    workers = min(requested_workers, len(jobs))
    # 这是专用 CI/离线 runner 进程。启动并发前固定一次全局 shell 上限，避免多个任务
    # 分别临时设置/还原同一环境变量产生竞态。
    os.environ.setdefault("HASHMM_SHELL_TIMEOUT_CAP", "600")
    # 在主线程初始化权限单例，确保并发任务共享同一个会话权限表。
    from hashmm.agent.permissions import get_permissions
    get_permissions()
    # 单独跑 SWE/Pro 时把并发额度用于逐题补丁生成；多个基准一起跑时默认不嵌套并发，
    # 将同时在途的重型 LLM 请求控制在 requested_workers 以内。显式环境变量仍优先。
    if "HASHMM_SWE_PATCH_WORKERS" not in os.environ:
        only_coding = len(jobs) == 1 and jobs[0][0] in {"swebench", "swebench_pro"}
        os.environ["HASHMM_SWE_PATCH_WORKERS"] = str(min(requested_workers, 3) if only_coding else 1)
    print(f"🚀 并发配置：基准并发 {workers}；SWE 补丁并发 {os.environ['HASHMM_SWE_PATCH_WORKERS']}")

    # 成对模式分两个阶段顺序执行，避免同一基准的裸模型轮和 Agent 轮同时
    # 修改共享工作区；阶段内部仍按 bench-workers 并发。baseline_scope 保证
    # 模式只在对应工作线程生效，不污染同进程里的其他任务。
    for is_baseline in phase_modes:
        phase_label = "裸模型基线" if is_baseline else "Agent"
        print(f"\n══ {phase_label}阶段 ══")
        futures = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hashmm-bench") as pool:
            for b, bid in jobs:
                print(f"▶ 提交 {b}（{phase_label} · {args.sample}）…")
                futures[pool.submit(_run_one, b, bid, args.sample, llm, is_baseline)] = (b, bid)

            for future in as_completed(futures):
                b, bid = futures[future]
                try:
                    _, _, res, dt = future.result()
                except Exception as exc:  # noqa: BLE001
                    res = {
                        "id": bid, "name": b, "kind": "official", "mode": "full",
                        "skip": True, "score_pct": None, "baseline": is_baseline,
                        "comparable": False,
                        "detail": f"并发工作线程异常：{type(exc).__name__}: {exc}",
                    }
                    dt = 0
                # 无论成败都把结果落盘；成对模式固定使用 _baseline 后缀区分两轮。
                fp = out_dir / f"{bid}{'_baseline' if is_baseline else ''}.json"
                fp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
                saved.append(str(fp))
                if res.get("skip"):
                    print(f"  ⏭️ 跳过：{res.get('detail', '')[:160]}")
                    for failure in (res.get("fails") or [])[:5]:
                        print(f"     失败样例：{failure}")
                    try:
                        _bh = Path(os.environ.get("HASHMM_BENCH_HOME")
                                   or (Path.home() / "hashmm-benchmarks"))
                        if _bh.exists():
                            _names = sorted(p.name for p in _bh.iterdir())[:24]
                            print(f"     🔎 BENCH_HOME({_bh}) 现有：{', '.join(_names) or '（空）'}")
                        else:
                            print(f"     🔎 BENCH_HOME({_bh}) 不存在——安装步骤没产出任何东西")
                    except Exception:  # noqa: BLE001
                        pass
                    if b in _OPTIONAL_EXTERNAL_ENV:
                        print("     ℹ️ 这是缺少可选外部运行环境的正常 SKIP，不标记工作流失败")
                    else:
                        rc = 1
                    continue
                print(f"  📊 {res.get('name', b)}: {res.get('score_pct')}% "
                      f"（{res.get('passed')}/{res.get('total')}）· 用时 {dt}s")
                if res.get("detail"):
                    print(f"     详情：{res['detail']}")
                for failure in (res.get("fails") or [])[:5]:
                    print(f"     失败样例：{failure}")
                if res.get("pipeline_ok") is False:
                    print("     ❌ 评测管线未完整完成（分数仍已留档，但本次工作流标红）")
                    rc = 1
                if not args.no_ingest:
                    _ingest(res)
    if saved:
        print(f"\n📁 结果已落盘 {len(saved)} 份：{', '.join(saved)}")
        print("   服务器在内网收不到回传？去 Actions 运行页底部 Artifacts 下载 bench-results，")
        print("   回内网在「测试中枢 → 和大厂对比 → 导入CI结果」上传这些 JSON 即可入库。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

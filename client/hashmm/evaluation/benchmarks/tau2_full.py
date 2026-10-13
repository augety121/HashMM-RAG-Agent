"""τ²-bench（tau-bench）官方 harness 对接 —— V306。

★ 好消息：tau-bench 是**纯 Python，不需要 Docker** —— 所以在你的 AutoDL 上能跑出**官方口径的真分**。

做法：
  · `install.sh tau2` 把官方仓库 clone 到 `$HASHMM_BENCH_HOME/tau-bench` 并装进**独立 venv**
    （`tau2-venv`，绝不碰你后端主环境）；
  · 本模块把你后端**当前激活的模型**（base_url / model_name / api_key）通过 **OpenAI 兼容协议**
    喂给 tau-bench 的 litellm（`--model-provider openai` + `OPENAI_BASE_URL/OPENAI_API_KEY`）；
  · 直接调它的官方 `run.py` → 官方评分（reward==1 才算成功）→ 解析结果 JSON → Pass@1。

任务量：retail 115 题 / airline 50 题（test split）。默认只跑前 N 题（可配），因为每题都是多轮对话，
会真实消耗你的 token。

纯标准库；缺 venv/仓库/模型配置时明确 SKIP，绝不假造分数。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path


def _sh(cmd: list[str], cwd: str | None = None, env: dict | None = None,
        timeout: int = 5400) -> tuple[int, str, str]:
    try:
        e = {**os.environ, **(env or {})}
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as ex:  # noqa: BLE001
        return 1, "", f"{type(ex).__name__}: {ex}"


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def repo_dir() -> Path:
    return bench_home() / "tau-bench"


def venv_python() -> str | None:
    p = bench_home() / "tau2-venv" / "bin" / "python"
    return str(p) if p.exists() else None


def active_model() -> dict:
    """从后端取当前激活模型的 base_url / model_name / api_key（喂给 tau-bench 的 litellm）。

    ★ 修 bug：旧代码用 db.list_models()，而 list_models 的 SELECT 里【没有 api_key】
    （api_key 加密存在 api_key_enc，只有 get_default_model/get_model 会解密带出）。
    于是 has_model 恒为 False → τ²-bench 永远报「后端没有可用的模型配置」跑不起来。
    改用 get_default_model()（含解密后的 api_key）；无默认模型时回退首个已配模型。
    """
    try:
        from hashmm.api import database as db
        m = None
        try:
            m = db.get_default_model()
        except Exception:  # noqa: BLE001
            m = None
        if m and m.get("api_key") and m.get("model_name"):
            return dict(m)
        # 回退：列表里挑第一个，再用 get_model 解出它的 api_key
        for row in (db.list_models() or []):
            mid = row.get("id")
            if not mid:
                continue
            try:
                full = db.get_model(mid)
            except Exception:  # noqa: BLE001
                full = None
            if full and full.get("api_key") and full.get("model_name"):
                return dict(full)
        return dict(m) if m else {}
    except Exception:  # noqa: BLE001
        return {}


def detect() -> dict:
    repo = repo_dir().is_dir()
    venv = venv_python() is not None
    m = active_model()
    has_model = bool(m.get("api_key") and m.get("model_name"))
    ok = repo and venv and has_model
    if ok:
        hint = ""
    elif not repo or not venv:
        hint = "未安装 τ²-bench：在后端跑 `bash hashmm/evaluation/benchmarks/install.sh tau2`（纯 Python，不需要 Docker）"
    else:
        hint = "后端没有可用的模型配置（api_key/model_name），无法驱动 τ²-bench"
    return {"installed": ok, "repo": repo, "venv": venv, "model": has_model, "hint": hint}


def build_cmd(py: str, env_name: str, model: str, n: int, log_dir: str) -> list[str]:
    """构造官方 run.py 命令（抽出便于单测）。

    ★ agent 策略可用 HASHMM_TAU2_STRATEGY 覆盖（默认 tool-calling）。若你的模型不擅长
    OpenAI 原生函数调用，可试 act / react 等基于提示的策略（取决于所装 tau-bench 版本支持哪些）。

    ★ V311：model provider 可用 HASHMM_TAU2_PROVIDER 覆盖（默认 openai）。
    deepseek-v4-pro 这类模型走 litellm 的 openai 兼容层时，函数调用格式偶有不兼容
    （逐题报 API/函数调用层错误）——切到 litellm 的专属 provider 往往能解决：
        export HASHMM_TAU2_PROVIDER=deepseek
    run() 会自动把 {PROVIDER}_API_KEY / {PROVIDER}_API_BASE 注入子进程环境。
    """
    strategy = os.environ.get("HASHMM_TAU2_STRATEGY", "tool-calling")
    provider = (os.environ.get("HASHMM_TAU2_PROVIDER") or "openai").strip() or "openai"
    # V324：官方 harness 的并发度也用统一开关 HASHMM_BENCH_CONCURRENCY（默认 4）。
    from .parallel import bench_concurrency
    _conc = str(bench_concurrency())
    return [
        py, "run.py",
        "--agent-strategy", strategy,
        "--env", env_name,
        "--model", model,
        "--model-provider", provider,       # 默认 openai 兼容 → 走 OPENAI_BASE_URL
        "--user-model", model,
        "--user-model-provider", provider,
        "--user-strategy", "llm",
        "--task-split", "test",
        "--start-index", "0",
        "--end-index", str(n),
        "--max-concurrency", _conc,
        "--log-dir", log_dir,
    ]


def harness_probe(py: str, auto_fix: bool = True) -> tuple[bool, str]:
    """★ V310：起跑前检查 τ² 官方 harness 自己的依赖能否 import，缺了就自动补装。

    真凶记录：上一轮 0/10 的逐题错误是 `NotFoundError: No module named 'fastapi'` ——
    官方 harness 自身缺依赖，10 题全部空轨迹返回 reward=0。而 harness 逐题吞异常照写 0 分，
    所以表面上看是"模型一题都做不出"，实际它压根没跑起来。install.sh 只做了
    `pip install -e tau-bench`，运行时需要的 fastapi 等没进 venv。

    这里：先探测 → 缺则自动 pip 装（走国内镜像）→ 再探测。仍失败就返回确切的手工命令。
    """
    if not py or not Path(py).exists():
        return False, "τ² venv 不存在"
    # tau-bench / tau2-bench 运行期真正会 import 的核心依赖。
    # 打地鼠记录：V310 缺 fastapi、V311 用户现场缺 orjson——静态清单永远慢一步，
    # 所以 V312 另加了通用自愈（run() 里检测 "No module named 'X'" → 补装 X 重跑一次）。
    need = ["fastapi", "litellm", "pydantic", "orjson", "openai"]
    probe_code = (
        "import importlib.util;"          # 必须 import importlib.util（import importlib 拿不到 util）
        "missing=[m for m in %r if importlib.util.find_spec(m) is None];"
        "print('MISSING:'+','.join(missing))" % (need,)
    )
    rc, out, err = _sh([py, "-c", probe_code], timeout=120)
    if rc != 0:
        return False, f"harness 依赖探测失败：{(err or out)[:120]}"
    missing = [m for m in (out.split("MISSING:")[-1].strip().split(",")) if m]
    if not missing:
        return True, "harness 依赖齐全"
    if not auto_fix:
        return False, f"τ² venv 缺依赖：{', '.join(missing)}"

    # 自动补装（国内镜像；失败不抛异常）
    idx = os.environ.get("HASHMM_PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    rc2, out2, err2 = _sh([py, "-m", "pip", "install", "-q", "-i", idx,
                           "--trusted-host", "pypi.tuna.tsinghua.edu.cn", *missing],
                          timeout=900)
    rc3, out3, _ = _sh([py, "-c", probe_code], timeout=120)
    still = [m for m in (out3.split("MISSING:")[-1].strip().split(",")) if m] if rc3 == 0 else missing
    if not still:
        return True, f"harness 依赖已自动补装：{', '.join(missing)}"
    return False, (f"τ² venv 缺依赖且自动补装失败：{', '.join(still)}。"
                   f"请手动跑：{py} -m pip install {' '.join(still)}"
                   f"（错误：{(err2 or out2)[:100]}）")


# 模块名 → pip 包名（import 名与安装名不一致的常见对）。
_MOD_TO_PIP = {"yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python-headless",
               "sklearn": "scikit-learn", "dotenv": "python-dotenv", "bs4": "beautifulsoup4"}
_MOD_GAP_RE = re.compile(r"No module named ['\"]?([A-Za-z0-9_\.]+)['\"]?")


def missing_module(*texts: str) -> str:
    """从错误文本里识别缺失的顶层模块名；没有返回空串。"""
    for t in texts:
        m = _MOD_GAP_RE.search(t or "")
        if m:
            return m.group(1).split(".")[0]
    return ""


def venv_install(py: str, module: str) -> bool:
    """把缺失模块补装进 τ² venv（默认国内镜像，可用 HASHMM_PIP_INDEX 覆盖）。"""
    if not module:
        return False
    pkg = _MOD_TO_PIP.get(module, module)
    idx = os.environ.get("HASHMM_PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    host = idx.split("//", 1)[-1].split("/", 1)[0]
    rc, _, _ = _sh([py, "-m", "pip", "install", "-q", "-i", idx,
                    "--trusted-host", host, pkg], timeout=600)
    if rc != 0:   # 镜像失败退回官方源再试一次
        rc, _, _ = _sh([py, "-m", "pip", "install", "-q", pkg], timeout=600)
    chk, out, _ = _sh([py, "-c", f"import importlib.util;print(importlib.util.find_spec({module!r}) is not None)"],
                      timeout=60)
    return chk == 0 and "True" in (out or "")


def preflight_probe(base: str, api_key: str, model: str, timeout: int = 45) -> tuple[bool, str]:
    """起跑前直测「OpenAI 兼容 chat + 函数调用」是否真的通（纯 stdlib，不引 litellm）。

    ★ V309 动机：τ² 连续多轮 0/10，而官方 harness 逐题吞异常照样写 reward=0 —— API 报错
    （鉴权/模型名不存在/不支持 tools/限流）会被无声吃掉，报告只剩个 0。先花一次调用把
    这些问题在起跑前暴露出来：失败 → 直接 SKIP 并给出确切原因，不再白烧 10 题 token。
    返回 (ok, 说明)。
    """
    import urllib.error
    import urllib.request
    if not (base and api_key and model):
        return False, "缺 base_url/api_key/model"
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "调用工具 ping，参数 x=1。"}],
        "tools": [{"type": "function", "function": {
            "name": "ping", "description": "连通性测试",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}},
                           "required": ["x"]}}}],
        "tool_choice": "auto",
        "max_tokens": 64,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:  # noqa: PERF203
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"API HTTP {e.code}：{detail or e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"API 不可达：{type(e).__name__}: {str(e)[:150]}"
    try:
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        if msg.get("tool_calls"):
            return True, "chat+tools 探测通过（模型会发起函数调用）"
        # 有响应但没走函数调用 —— API 通但 tool-calling 能力存疑，提示可换策略
        return True, ("chat 通但探测请求未触发函数调用——若跑分仍为 0，"
                      "试 HASHMM_TAU2_STRATEGY=react（提示词式策略，不依赖原生函数调用）")
    except Exception:  # noqa: BLE001
        return False, f"API 响应格式异常：{str(body)[:150]}"


def extract_errors(log_dir: Path, max_items: int = 3) -> list[str]:
    """从官方结果 JSON 的逐题轨迹里提取错误串（API 报错/异常）——0 分时给出'为什么'。"""
    out: list[str] = []
    try:
        files = sorted(log_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not (isinstance(d, list) and d and isinstance(d[0], dict) and "reward" in d[0]):
                continue
            for item in d:
                blob = json.dumps(item, ensure_ascii=False)
                for pat in ("Error code:", "APIError", "BadRequest", "RateLimit",
                            "AuthenticationError", "NotFoundError", "Traceback",
                            "litellm.exceptions"):
                    i = blob.find(pat)
                    if i >= 0:
                        snippet = blob[i:i + 160].replace("\\n", " ")
                        if snippet not in out:
                            out.append(snippet)
                        break
                if len(out) >= max_items:
                    return out
            break
    except Exception:  # noqa: BLE001
        pass
    return out


def parse_results(log_dir: Path) -> dict:
    """官方结果：JSON 数组，每项含 reward。reward==1 即任务成功（官方口径）。"""
    files = sorted(log_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, list) and d and isinstance(d[0], dict) and "reward" in d[0]:
            total = len(d)
            ok = sum(1 for r in d if float(r.get("reward", 0)) >= 1 - 1e-6)
            return {"passed": ok, "total": total,
                    "pass_rate": round(100.0 * ok / max(1, total), 1)}
    return {"passed": 0, "total": 0, "pass_rate": 0.0, "error": "未找到含 reward 的结果文件"}


def run(adapter, *, limit: int = 10, env_name: str | None = None) -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    m = active_model()
    py = venv_python()
    env_name = env_name or os.environ.get("HASHMM_TAU2_ENV", "retail")
    from .sample_stats import resolve_limit
    n = resolve_limit("tau2", limit)
    log_dir = bench_home() / "tau2-runs" / f"run_{int(time.time())}"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ★ τ² 专属模型覆盖：τ² 是纯 Python·免 Docker 的，0 分几乎都是"模型不够强/工具调用不稳"。
    # 想拿到真实 τ² 分数，可用下面这组环境变量把 τ² 单独指到一个更强的模型（如 GPT-4o/Claude），
    # 不影响后端默认模型。三者都不设时，沿用后端当前默认模型。
    ov_model = os.environ.get("HASHMM_TAU2_MODEL", "").strip()
    ov_base  = os.environ.get("HASHMM_TAU2_BASE_URL", "").strip()
    ov_key   = os.environ.get("HASHMM_TAU2_API_KEY", "").strip()
    model_name = ov_model or str(m.get("model_name") or "")
    base = (ov_base or str(m.get("base_url") or "")).rstrip("/")
    api_key = ov_key or str(m.get("api_key") or "")
    sub_env = {
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": base,
        "OPENAI_API_BASE": base,        # litellm 两个变量名都读
        "LITELLM_LOG": "ERROR",
    }
    # ★ V311：HASHMM_TAU2_PROVIDER 切非 openai provider 时，litellm 读的是
    # {PROVIDER}_API_KEY / {PROVIDER}_API_BASE（如 DEEPSEEK_API_KEY）——自动注入，
    # 用户只需设 provider 一个变量，key/base 沿用后端已配置的。
    _prov = (os.environ.get("HASHMM_TAU2_PROVIDER") or "openai").strip() or "openai"
    if _prov.lower() != "openai":
        _pu = "".join(c if c.isalnum() else "_" for c in _prov.upper())
        sub_env[f"{_pu}_API_KEY"] = api_key
        sub_env[f"{_pu}_API_BASE"] = base
    # ★ V310：先检查 harness 自己的依赖（上一轮 0/10 的真凶就是这里：venv 缺 fastapi，
    # 官方 harness 逐题吞异常照写 reward=0 → 看起来像"模型一题都不会"，实际压根没跑起来）。
    hp_ok, hp_msg = harness_probe(py)
    if not hp_ok:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": f"τ²-bench harness 自身依赖不全（未起跑，未烧 token）：{hp_msg}"}

    # ★ V309：起跑前预检（API 可达 + 鉴权 + 函数调用能力）。挡掉"逐题吞异常 → 稳定 0 分黑盒"。
    probe_ok, probe_msg = preflight_probe(base, api_key, model_name)
    if not probe_ok:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": f"τ²-bench 预检失败（未起跑，未烧 token）：{probe_msg}。"
                          f"检查后端默认模型的 base_url/api_key/model_name，"
                          f"或用 HASHMM_TAU2_MODEL/BASE_URL/API_KEY 单独指定评测模型。"}

    # ★ V312 通用自愈：静态依赖清单永远慢一步（V310 缺 fastapi、V311 现场缺 orjson）。
    # 跑完若 0 分/无结果且错误匹配 "No module named 'X'" → 把 X 装进 venv、自动重跑一次。
    # 每次尝试用独立 log 子目录——同目录重跑会让 parse_results 混入上一轮的 0 分结果。
    _heal_on = (os.environ.get("HASHMM_TAU2_SELFHEAL", "1").strip().lower()
                not in ("0", "false", "off", "no"))
    healed_note = ""
    rc = -1
    out = err = ""
    rep = {"total": 0, "passed": 0, "pass_rate": 0.0}
    errs: list[str] = []
    attempt_dir = log_dir
    for attempt in (1, 2):
        attempt_dir = log_dir / f"attempt{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(py, env_name, model_name, n, str(attempt_dir))
        # 超时随题数缩放：每题给 150s 预算，至少 2 小时。避免 full(115 题) 这类大跑
        # 在慢速本地模型上被固定超时误杀（此前固定 7200s，115 题很可能不够）。
        _to = max(7200, int(n) * 150)
        rc, out, err = _sh(cmd, cwd=str(repo_dir()), env=sub_env, timeout=_to)
        rep = parse_results(attempt_dir)
        errs = extract_errors(attempt_dir) if rep["total"] else []
        if attempt == 1 and _heal_on and (rep["total"] == 0 or rep["passed"] == 0):
            gap = missing_module("\n".join(errs[:5]), err or "", (out or "")[-2000:])
            if gap:
                ok_fix = venv_install(py, gap)
                healed_note = (f"检测到 venv 缺模块 {gap} → "
                               f"{'已补装并自动重跑' if ok_fix else '补装失败（手工: <venv>/bin/pip install ' + gap + '）'}")
                if ok_fix:
                    continue
        break

    if rep["total"] == 0:
        _d = f"τ²-bench 未产出有效结果（rc={rc}）：{(err or out)[-300:]}"
        if healed_note:
            _d = f"[自愈] {healed_note}。{_d}"
        return {"kind": "official", "skip": True, "score_pct": None, "detail": _d}
    from .sample_stats import format_breakdown
    bd = {"环境": env_name, "判分口径": "官方 harness（reward==1 才算成功）",
          "预检": probe_msg, "harness依赖": hp_msg,
          **format_breakdown(rep["passed"], rep["total"], "tau2")}
    if healed_note:
        bd["自愈"] = healed_note
    fails: list[str] = []
    if rep["passed"] == 0:
        errs = errs or extract_errors(attempt_dir)
        bd["provider"] = (os.environ.get("HASHMM_TAU2_PROVIDER") or "openai").strip() or "openai"
        if errs:
            # ★ V311：错误原文完整暴露。此前截 70 字，"NotFoundError: No m…" 这种
            # 半句话根本无法诊断——0 分基准的第一要务是让你看到真凶。
            bd["0分诊断"] = ("逐题错误发生在 API/函数调用层（非任务本身失败）。首条完整错误见下；"
                            "若是模型名/函数调用不兼容 → export HASHMM_TAU2_PROVIDER=deepseek "
                            "或 HASHMM_TAU2_STRATEGY=react 再跑")
            bd["首条完整错误"] = errs[0][:500]
            fails = [f"τ²: {e[:300]}" for e in errs[:5]]
        else:
            bd["0分诊断"] = ("轨迹里没有 API 报错 → 是策略问题：模型没按业务规则完成任务。"
                            "试 HASHMM_TAU2_STRATEGY=react，或用 HASHMM_TAU2_MODEL 指定更强模型")
        # ★ V311：harness 自身 stderr 尾部也进报告——litellm 不认模型名、鉴权失败
        # 这类真凶往往只出现在 stderr，结果 JSON 里看不到。
        if (err or "").strip():
            bd["harness stderr 尾部"] = err.strip()[-500:]
    return {
        "kind": "official", "skip": False,
        "passed": rep["passed"], "total": rep["total"], "score_pct": rep["pass_rate"],
        "detail": f"官方数据集（tau-bench {env_name} test，前 {n} 题，官方 reward 判分）",
        "breakdown": bd,
        "fails": fails,
    }

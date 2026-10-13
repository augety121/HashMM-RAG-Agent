"""SWE-bench —— **无 Docker 的本机评测模式**（V306）。

背景：官方 harness 用 Docker 给每个仓库做依赖隔离。你的 AutoDL 容器没有 Docker，
于是官方模式跑不了。本模块给出**不需要 Docker 的替代路径**：

  clone 仓库@base_commit → 建**独立 venv**（每个实例一个，装在 $HASHMM_BENCH_HOME 下）
  → pip install 该仓库 → 让 agent 改代码 → 打上官方 test_patch
  → 用 pytest 跑官方指定的 FAIL_TO_PASS / PASS_TO_PASS → 官方判定规则算 resolved。

**诚实标注**：判定规则与官方一致（F2P 全过 + P2P 全过 才算 resolved），但**环境不是官方 Docker
镜像**，而是本机 venv 现装依赖。某些仓库（需要系统库/特定版本编译）会装不上 → 这些实例会被
如实标为 `env_error` 并**从分母里剔除**（不当成失败，也不当成成功），报告里会写清楚跳过了几个。
所以分数标为 **"本机执行(非官方Docker口径)"**，可作参考，不等同官方复现值。

绝不动你的主环境：所有 venv/仓库都在 $HASHMM_BENCH_HOME 下。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _sh(cmd: list[str], cwd: str | None = None, env: dict | None = None,
        timeout: int = 900) -> tuple[int, str, str]:
    try:
        e = {**os.environ, **(env or {})}
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as ex:  # noqa: BLE001
        return 1, "", f"{type(ex).__name__}: {ex}"


from . import experience as _EXP
from ._paths import bench_home
from .sample_stats import format_breakdown as _fmt_bd  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def dataset_path() -> Path:
    return bench_home() / "swebench_verified.jsonl"


def detect() -> dict:
    ds = dataset_path().exists()
    # 预置镜像目录里有几个仓库（零网络方案就绪度）
    n_preload = 0
    for _root in ([Path(os.environ["HASHMM_SWEBENCH_REPO_CACHE"])]
                  if os.environ.get("HASHMM_SWEBENCH_REPO_CACHE") else []) + \
                 [bench_home() / "swebench-repos-preload"]:
        if _root.is_dir():
            n_preload += sum(1 for d in _root.iterdir()
                             if d.is_dir() and ((d / "HEAD").exists() or (d / ".git").exists()))
    hint = "" if ds else "未装 SWE-bench 数据集：运行 benchmarks/install.sh swebench"
    if ds and n_preload == 0:
        hint = ("提示：直连 github clone 不稳时，可在有网机器跑 install.sh swebench_preload "
                "预置仓库、拷到 bench 目录 → SWE 跑分零网络（当前预置镜像 0 个）")
    return {"installed": ds, "n_preload": n_preload, "hint": hint}


# 无 Docker 本机 venv 能干净装上的仓库（纯 Python / 有二进制 wheel，不需要现场编译 C 扩展）。
# SWE-bench Verified 里 astropy/matplotlib/scikit-learn 等需要编译系统库，本机 pip 大概率失败；
# 优先挑这些能装的，本机模式才跑得出分（而不是"前 3 个恰好都是重仓库 → 全 env_error → 无分"）。
# 只挑本机 venv 能快速 clone + 装上 + 测试集不庞大的仓库。
# ★ 去掉 django/xarray：django 仓库巨大、单实例常需过数百个测试（如 django-10097 要过 438 个），
#   本机 clone 慢且易失败，通用 agent 几乎不可能全过——放它进来只会白跑一场 0 分。
#   优先纯 Python 小库（requests/flask/marshmallow/click/pytest），clone 快、依赖少、测试集小。
_LOCAL_FRIENDLY_REPOS = (
    "psf/requests", "pallets/flask", "pallets/click",
    "marshmallow-code/marshmallow", "pytest-dev/pytest",
    "pylint-dev/pylint", "sphinx-doc/sphinx", "PyCQA/flake8",
    "sympy/sympy", "pvlib/pvlib-python",
)


# 本机模式最低实例年份：更早的实例依赖古董构建链，Python 3.10+ 上装不起来（如实跳过而非假失败）。
_MIN_YEAR = int(os.environ.get("HASHMM_SWEBENCH_MIN_YEAR", "2020"))

# ★ V310 步数预算（对标大厂 harness）
_ITERS = int(os.environ.get("HASHMM_SWEBENCH_MAX_ITERS", "80"))
_TOOLCALLS = int(os.environ.get("HASHMM_SWEBENCH_MAX_TOOLCALLS", "200"))
_EXECS = int(os.environ.get("HASHMM_SWEBENCH_MAX_EXECS", "50"))

_SELF_CHECK = (
    "现在自查：① 你改的代码是否真的解决了 issue 描述的问题？"
    "② 用 .venv/bin/python -m pytest 跑一下相关测试，确认没有引入新的失败"
    "（改动不能破坏原本能过的测试——这是 SWE-bench 的判分规则之一）。"
    "③ 发现问题就继续修。全部确认后回复「已完成」。"
)


def _instance_year(inst: dict) -> int:
    """从 created_at 取年份（取不到按 0 处理 → 排最后）。"""
    raw = str(inst.get("created_at") or "")
    m = re.search(r"(19|20)\d{2}", raw)
    return int(m.group(0)) if m else 0


def _python_for_year(year: int) -> str:
    """按实例年代选 Python 版本（conda 可用时用；对标官方 spec map 的做法）。

    SWE-bench 官方给每个 repo+version 指定 Python 版本（Docker 镜像里预置）。本机模式
    没有官方镜像，就按年代近似：老实例需要老 Python，否则构建链对不上。
    """
    if year >= 2022:
        return "3.11"
    if year >= 2020:
        return "3.9"
    if year >= 2018:
        return "3.8"
    return "3.6"


def _conda_bin() -> str | None:
    """找一个当前用户确实能执行的 conda；没有则回落到系统 venv。

    Actions runner 可能看得到 `/root/miniconda3/bin/conda`，但 runner 用户没有执行
    权限。旧版只检查 Path.exists()，不仅会误选这个路径，Path.exists() 本身还可能因
    `/root` 的目录权限抛 PermissionError，导致整个 SWE-bench Pro 被跳过。
    """
    candidates = (
        os.environ.get("CONDA_EXE"),
        "/root/miniconda3/bin/conda",
        "/opt/conda/bin/conda",
        "/usr/share/miniconda/bin/conda",  # GitHub hosted runner 常见位置
        shutil.which("conda"),
    )
    for c in dict.fromkeys(c for c in candidates if c):
        try:
            path = Path(c)
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
        except OSError:
            continue
        # 权限位并不保证运行期可执行（ACL/noexec mount 等）；做一次无副作用探测。
        rc, out, err = _sh([str(path), "--version"], timeout=15)
        if rc == 0 and "conda" in f"{out}\n{err}".lower():
            return str(path)
    return None


def load_instances(limit: int = 3, dataset_file: "Path|None" = None) -> list[dict]:
    p = dataset_file or dataset_path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    if not limit:
        return rows
    # 稳定排序：本机友好仓库优先，其余按原序垫后（不丢任何实例，只调顺序）。
    # 允许用 HASHMM_SWEBENCH_ALL_REPOS=1 关闭偏好（想严格按数据集原序时用）。
    import os as _os
    if _os.environ.get("HASHMM_SWEBENCH_ALL_REPOS") == "1":
        return rows[:limit]
    def _rank(inst):
        # ★ V310：本机模式的真实约束是【实例年代】，不是仓库。
        # psf/requests-1142/1724 是 2013 年的实例 —— 那个年代的包需要古董 setuptools，
        # 在 Python 3.12 上编译必然失败（subprocess-exited-with-error）。这就是之前
        # "3 个实例全部环境搭建失败"的真因：仓库在白名单里，但实例太老。
        # 排序：本机友好仓库 + 现代实例（2020+）优先；同级按 created_at 新→旧。
        repo_ok = str(inst.get("repo", "")) in _LOCAL_FRIENDLY_REPOS
        year = _instance_year(inst)
        modern = year >= _MIN_YEAR
        return (0 if (repo_ok and modern) else 1 if modern else 2, -year)
    rows.sort(key=_rank)   # Python sort 稳定，同 rank 内保持原序
    return rows[:limit]


def _as_list(v) -> list[str]:
    """FAIL_TO_PASS/PASS_TO_PASS 可能是 JSON 字符串或列表。"""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return [str(x) for x in d] if isinstance(d, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _git_mirrors(repo: str) -> list[str]:
    """clone 源列表：HASHMM_GIT_MIRRORS（逗号分隔前缀，如 https://ghfast.top/）优先，
    再接内置国内可用前缀，最后直连 github。前缀式镜像 = 前缀 + 完整 github URL。"""
    gh = f"https://github.com/{repo}.git"
    urls: list[str] = []
    env = os.environ.get("HASHMM_GIT_MIRRORS", "").strip()
    for pre in [p.strip() for p in env.split(",") if p.strip()]:
        urls.append(pre.rstrip("/") + "/" + gh if not pre.endswith(".git") else pre)
    urls += [
        f"https://ghfast.top/{gh}",
        f"https://gh-proxy.com/{gh}",
        f"https://ghproxy.net/{gh}",
        f"https://gitclone.com/github.com/{repo}.git",
        gh,
    ]
    return list(dict.fromkeys(urls))


def _repo_cache_dir(repo: str) -> Path:
    return bench_home() / "swebench-repos" / repo.replace("/", "__")


def clone_repo(repo: str, work: Path, base_commit: str) -> tuple[bool, str]:
    """clone 仓库到 work 并 checkout base_commit。返回 (ok, 说明)。

    ★ V313 修"12/12 全部环境失败"的核心：**仓库级本地缓存**。
    此前每个实例都对着镜像做一次完整 clone——国内网络下镜像时好时坏、大仓库
    动辄数分钟，12 个实例就是 12 次赌博。现在：首个实例把仓库 clone 进
    bench_home()/swebench-repos/<owner__repo>（完整历史），之后同仓库的所有
    实例走 `git clone --local` 秒级完成、零网络。缓存里没有目标 commit 时
    自动 fetch 刷新一次再试。每个镜像 fail-fast（HASHMM_GIT_CLONE_TIMEOUT，
    默认 600s），死镜像不再拖满 30 分钟。
    """
    cache = _repo_cache_dir(repo)
    t_out = int(os.environ.get("HASHMM_GIT_CLONE_TIMEOUT", "600"))

    def _fill_work_from_cache() -> tuple[bool, str]:
        _sh(["rm", "-rf", str(work)])
        rc, _, err = _sh(["git", "clone", "--local", str(cache), str(work)], timeout=300)
        if rc != 0:
            return False, f"本地缓存 clone 失败：{(err or '')[:100]}"
        rc, _, err = _sh(["git", "checkout", "-f", base_commit], cwd=str(work), timeout=180)
        if rc == 0:
            return True, ""
        # 缓存可能是旧的（没有该 commit）→ 刷新一次缓存再试
        _sh(["git", "-C", str(cache), "fetch", "--all", "--tags"], timeout=t_out)
        rc, _, err = _sh(["git", "checkout", "-f", base_commit], cwd=str(work), timeout=180)
        return (rc == 0), ("" if rc == 0 else f"checkout 失败：{(err or '')[:100]}")

    if (cache / "HEAD").exists() or (cache / ".git").exists():
        ok, why = _fill_work_from_cache()
        if ok:
            return True, "clone=本地缓存"
        # 缓存坏了 → 删掉走网络重建
        _sh(["rm", "-rf", str(cache)])
        pass  # 缓存坏 → 下方走网络重建

    # ★ V316 零网络兜底：用户在有网机器 clone 好、拷到 AutoDL 的【预置镜像目录】。
    # HASHMM_SWEBENCH_REPO_CACHE=<目录> 或默认 bench_home()/swebench-repos-preload/，
    # 目录下按 owner__repo（psf__requests）或 repo 名放库均可。彻底摆脱网络赌博。
    _preload_roots = []
    _pr = os.environ.get("HASHMM_SWEBENCH_REPO_CACHE", "").strip()
    if _pr:
        _preload_roots.append(Path(_pr))
    _preload_roots.append(bench_home() / "swebench-repos-preload")
    for _root in _preload_roots:
        for _name in (repo.replace("/", "__"), repo.split("/")[-1]):
            _cand = _root / _name
            if _cand.is_dir() and ((_cand / "HEAD").exists() or (_cand / ".git").exists()):
                _sh(["rm", "-rf", str(work)])
                rc, _, _e = _sh(["git", "clone", "--local", str(_cand), str(work)], timeout=300)
                if rc == 0:
                    rc, _, _e = _sh(["git", "checkout", "-f", base_commit], cwd=str(work), timeout=180)
                    if rc == 0:
                        return True, f"clone=预置镜像({_cand.name})·零网络"

    cache.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    for url in _git_mirrors(repo):
        _sh(["rm", "-rf", str(cache)])
        rc, _, err = _sh(["git", "clone", "--mirror", url, str(cache)], timeout=t_out)
        if rc == 0:
            ok, why = _fill_work_from_cache()
            if ok:
                src = url.split("//", 1)[-1].split("/", 1)[0]
                return True, f"clone={src}（已入本地缓存，同仓库后续实例零网络）"
            last = why
            continue
        last = (err or "")[:120]
    return False, (f"clone 失败（{len(_git_mirrors(repo))} 个源全试过，含本地缓存）：{last[:150]}。"
                   f"可设 HASHMM_GIT_MIRRORS=<你可用的加速前缀> 追加镜像")


def prepare_env(inst: dict, work: Path) -> tuple[bool, str, str]:
    """clone@base_commit + 建 venv + 装依赖。返回 (ok, venv_python, 说明)。"""
    repo, base = inst.get("repo", ""), inst.get("base_commit", "")
    if not repo or not base:
        return False, "", "实例缺 repo/base_commit"
    ok, clone_note = clone_repo(repo, work, base)
    if not ok:
        return False, "", f"env_error: {clone_note}"

    # ── 建 Python 环境 ──
    # ★ V310：按【实例年代】选 Python 版本。官方 SWE-bench 给每个 repo+version 指定 Python
    # 并预置在 Docker 镜像里；本机模式没有镜像，就用 conda 按年代近似（AutoDL 自带 miniconda）。
    # 这是"依赖装不上"的正解：2013 年的 requests 在 Python 3.12 上必然编译失败，不是它装不了，
    # 是【用错了 Python】。conda 不可用时回落到系统 venv（现代实例仍能装上）。
    year = _instance_year(inst)
    want_py = _python_for_year(year)
    conda = _conda_bin()
    py = ""
    env_note = ""
    if conda:
        env_name = f"swe_{inst.get('instance_id', 'x')}".replace("/", "_")[:60]
        rc, _, cerr = _sh([conda, "create", "-y", "-q", "-n", env_name,
                           f"python={want_py}"], timeout=1800)
        if rc == 0:
            cand = Path(conda).parent.parent / "envs" / env_name / "bin" / "python"
            if cand.exists():
                py = str(cand)
                env_note = f"conda py{want_py}(实例{year}年)"
        if not py:
            env_note = f"conda 建 py{want_py} 失败({cerr[:60]})→回落系统 venv"
    if not py:
        venv = work / ".venv"
        rc, _, err = _sh(["python3", "-m", "venv", str(venv)], timeout=180)
        if rc != 0:
            return False, "", f"venv 创建失败：{err[:120]}"
        py = str(venv / "bin" / "python")
        env_note = env_note or "系统 venv"

    # ★ pip 走国内镜像（可用 HASHMM_PIP_INDEX 覆盖），否则国内装依赖极慢/超时 → 误判 env_error。
    _idx = os.environ.get("HASHMM_PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    _pip = [py, "-m", "pip", "install", "-q", "-i", _idx,
            "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]
    # 老实例要老 setuptools（新 setuptools 移除了 2to3/use_2to3 等，老 setup.py 直接炸）
    if year and year < 2020:
        _sh(_pip + ["--upgrade", "pip"], timeout=300)
        _sh(_pip + ["setuptools<60", "wheel<0.38"], timeout=300)
    else:
        _sh(_pip + ["--upgrade", "pip", "setuptools", "wheel"], timeout=300)

    # 装仓库自身（editable）+ pytest；三级兜底，并【保留真实错误】（旧版截 100 字把根因丢了）
    attempts = [
        (["-e", ".", "pytest"], "editable"),
        ([".", "pytest"], "non-editable"),
        (["-e", ".", "pytest", "--no-build-isolation"], "no-build-isolation"),
    ]
    last_err = ""
    for args, how in attempts:
        rc, _out, err = _sh(_pip + args, cwd=str(work), timeout=1800)
        if rc == 0:
            return True, py, f"ok({env_note})"
        last_err = f"[{how}] {err or _out}"
    _key = _extract_pip_cause(last_err)
    return False, "", (f"env_error: 依赖装不上（{env_note}；实例 {year} 年）："
                       f"{_key or last_err[:200]}")


# pip 报错里的"废话包装行"——本身含 error 字样但零信息量，真因在它们之后/之前的具体行里。
_PIP_NOISE = ("subprocess-exited-with-error", "exit code", "See above for output",
              "note: This error originates", "Encountered error while",
              "error: metadata-generation-failed", "python setup.py egg_info did not run",
              "[end of output]", "[output was truncated]")
_PIP_CAUSE = ("No matching distribution", "Could not find a version", "Could not build",
              "Failed building wheel", "ModuleNotFoundError", "ImportError",
              "use_2to3", "SyntaxError", "fatal error:", "command 'gcc'", "command 'cc'",
              "unsupported Python version", "Requires-Python", "invalid command",
              "AttributeError", "NameError", "error in ", "RuntimeError")


def _extract_pip_cause(text: str) -> str:
    """从 pip 报错里抽出真因行（最多 2 行）。

    V312 修：旧版取【第一个】含 "error:" 的行——恰好命中 pip 的废话包装行
    `error: subprocess-exited-with-error`，真因（如 use_2to3 / gcc 失败 /
    Requires-Python 不满足）反而被丢了。现：拉黑包装行，按具体原因关键词
    自底向上找（pip 把根因放在输出末段），最多带 2 行。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    hits: list[str] = []
    for ln in reversed(lines):
        if any(n in ln for n in _PIP_NOISE):
            continue
        if any(k in ln for k in _PIP_CAUSE):
            hits.append(ln[:200])
            if len(hits) >= 2:
                break
    if hits:
        return " ｜ ".join(reversed(hits))
    # 没命中关键词：退而取最后一个非噪声、含 error/Error 的行
    for ln in reversed(lines):
        if ("error" in ln.lower()) and not any(n in ln for n in _PIP_NOISE):
            return ln[:200]
    return ""


# ── V314 反"巧合通过"（公开榜单审计显示存在空补丁/只改测试/
#     reward-hack 评测框架）。判定：agent 必须真的改了【非测试源码】，F2P/P2P 全过才计分。
_TEST_PATH = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]+$|_test\.py$|(^|/)conftest\.py$")


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(str(path).strip()))


def agent_changed_files(work: Path) -> list[str]:
    """agent 对工作区的实际改动清单（相对 checkout 后的 base：已跟踪改动 + 新增文件）。

    必须在 apply_test_patch **之前**调用——官方 test_patch 会改测试文件，混进来会污染判定。
    """
    files: list[str] = []
    rc, out, _ = _sh(["git", "diff", "--name-only"], cwd=str(work), timeout=60)
    if rc == 0:
        files += [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    rc, out, _ = _sh(["git", "ls-files", "--others", "--exclude-standard"], cwd=str(work), timeout=60)
    if rc == 0:
        files += [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return list(dict.fromkeys(files))


def touched_source(files: list[str]) -> bool:
    return any(not _is_test_path(f) for f in files)


def apply_test_patch(inst: dict, work: Path) -> bool:
    tp = inst.get("test_patch") or ""
    if not tp.strip():
        return True
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8") as f:
        f.write(tp if tp.endswith("\n") else tp + "\n")
        path = f.name
    rc, _, _ = _sh(["git", "apply", "-v", path], cwd=str(work), timeout=120)
    if rc != 0:
        rc, _, _ = _sh(["patch", "-p1", "-i", path], cwd=str(work), timeout=120)
    os.unlink(path)
    return rc == 0


def run_tests(py: str, work: Path, tests: list[str]) -> tuple[int, int]:
    """跑指定测试，返回 (通过数, 总数)。"""
    ok, n, _ = run_tests_detail(py, work, tests, capture=False)
    return ok, n


def run_tests_detail(py: str, work: Path, tests: list[str],
                     capture: bool = True) -> tuple[int, int, str]:
    """跑指定测试，返回 (通过数, 总数, 首个失败的输出尾部)。

    V312：评过的实例若失败，只有 "F2P 0/1, P2P 0/59" 这种计数没法诊断——
    P2P 全挂通常是补丁引入 SyntaxError/ImportError 把整个包搞炸了，而不是
    59 个测试各自失败。带上首个失败的 pytest 输出尾部，一眼看穿。
    """
    if not tests:
        return 0, 0, ""
    ok = 0
    first_fail = ""
    for t in tests:
        rc, out, err = _sh([py, "-m", "pytest", "-x", "-q", "--no-header", t],
                           cwd=str(work), timeout=600)
        if rc == 0:
            ok += 1
        elif capture and not first_fail:
            first_fail = ((out or "") + "\n" + (err or "")).strip()[-400:]
    return ok, len(tests), first_fail


def run(adapter, *, limit: int = 3) -> dict:
    """SWE-bench Verified 入口（无 Docker 本机模式）。"""
    return run_on_dataset(adapter, limit=limit)


def run_on_dataset(adapter, *, limit: int = 3, dataset_file: "Path|None" = None,
                   bench_key: str = "swebench", display: str = "SWE-bench Verified",
                   install_hint: str = "") -> dict:
    """通用本机执行核（V314 参数化）：Verified 与 Pro 共用全套机制——
    clone 本地缓存/镜像体系、按年代选 Python、env_error 回填、pip 真因提取、
    官方 F2P+P2P 判分、反巧合通过判定。缺数据集→SKIP；装不上→剔除并如实报告。"""
    ds = dataset_file or dataset_path()
    if not ds.exists():
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": install_hint or detect()["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit(bench_key, limit)
    # ★ V312 回填机制：env_error 不再消耗评测名额。从 limit×N 的候选池里抽，
    # 环境搭不起来就换下一个实例，直到【真正评上】limit 个或池子抽干——
    # 此前"配额 3 → 2 个 env_error → 只评出 1 例"，26 分钟的运行大半浪费。
    _mult = max(1, int(os.environ.get("HASHMM_SWE_POOL_MULT", "4")))
    pool = load_instances(limit * _mult, dataset_file=ds)
    if not pool:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": "数据集为空"}

    from hashmm.api.database import CONV_FILES_ROOT
    from hashmm.tools import agent_bench as AB

    resolved = 0
    evaluated = 0
    env_errors = 0
    attempted = 0
    suspects = 0            # 疑似巧合通过（测试过但没改源码）——不计分单列
    fails: list[str] = []
    # ★ V323 熔断：默认题数升到 50 后，池子是 50×4=200。若本机根本跑不了 SWE-bench
    # （无 Docker/网络差 → 环境全 env_error），别再把 200 个实例一个个磨完（会耗一小时）。
    # 前 _EARLY_ABORT 个实例全失败且一个都没评上 → 判定"本机环境跑不起来"，提前跳过。
    _EARLY_ABORT = max(3, int(os.environ.get("HASHMM_SWE_EARLY_ABORT", "8")))
    _aborted_early = False

    for inst in pool:
        if evaluated >= limit:
            break
        # 熔断：连续 _EARLY_ABORT 个环境都搭不起来、且尚无一个评上 → 本机跑不了，停。
        if evaluated == 0 and env_errors >= _EARLY_ABORT:
            _aborted_early = True
            break
        attempted += 1
        iid = inst.get("instance_id", "?")
        conv_id = f"swe-{iid}".replace("/", "_")[:80]
        work = Path(CONV_FILES_ROOT) / conv_id
        if work.exists():
            _sh(["rm", "-rf", str(work)])
        work.mkdir(parents=True, exist_ok=True)

        ok, py, msg = prepare_env(inst, work)
        if not ok:
            env_errors += 1
            if len(fails) < 5:
                fails.append(f"{iid}: {msg[:250]}")   # V311: 60→250，完整错误才能对症
            continue

        # ★ V309 环境预检：在 agent 动手【之前】抽查 P2P（这些测试按定义在 base_commit 上
        # 必须通过）。抽查不过 = 本机 venv 环境本身是坏的（依赖版本漂移/缺系统库），
        # 这锅不该扣在模型头上 → 如实标 env_error 并从分母剔除。此前 flask-5014 报
        # "P2P 0/59" 正是环境坏被当成模型 0 分的典型（P2P 全挂在数学上不可能是补丁造成的
        # 常规失败形态，而是测试根本跑不起来）。
        _p2p_all = _as_list(inst.get("PASS_TO_PASS"))
        _probe = _p2p_all[:3]
        if _probe:
            _pre_ok, _pre_n = run_tests(py, work, _probe)
            if _pre_ok < _pre_n:
                env_errors += 1
                if len(fails) < 5:
                    fails.append(f"{iid}: env_error: P2P 预检 {_pre_ok}/{_pre_n} 未过"
                                 f"（base_commit 上就跑不通=环境坏，非模型问题）")
                continue

        # 让 agent 在工作区里改代码（它的 read_file/str_replace/run_shell 就在这个目录）
        problem = str(inst.get("problem_statement", ""))[:6000]
        query = (f"你在一个已 clone 到当前工作区的 Git 仓库（{inst.get('repo')}）里工作。"
                 f"阅读下面的 issue，定位相关源文件并修改代码解决它。用工具直接改工作区里的文件。"
                 f"改完后运行相关测试自查（.venv/bin/python -m pytest <相关测试> -q），确认修复有效。\n\n"
                 f"=== ISSUE ===\n{problem}")
        try:
            # ★ V310 步数预算：SWE-bench 要「探仓库→定位文件→改→跑测试→看报错→再改」，
            # 默认 10 步 / 5 次执行远远不够（大厂 harness 给 50~200 步）。
            task = AB.Task(id=conv_id, category="swebench", turns=[query, _SELF_CHECK],
                           requires=set(), scorers=[AB.answer_nonempty(min_len=1)],
                           max_seconds=2400, max_iterations=_ITERS,
                           max_tool_calls=_TOOLCALLS, max_exec_calls=_EXECS)
            # ★ V309 修恒 0 分：conv_id 对齐（旧版 agent 在 bench-swe-* 空目录里"修"一个
            # 不存在的仓库）、preserve_workspace（不删克隆现场、不在判分前清场）、
            # 作用域 bypass（run_shell/git 操作不再被权限卡死）。
            AB.run_task(task, adapter.llm_fn, conv_id=conv_id,
                        preserve_workspace=True, permission_mode="bypass",
                        **_EXP.hint_kwargs(bench_key))   # V315 经验闭环（闸关=零差异）
        except Exception as e:  # noqa: BLE001
            if len(fails) < 5:
                fails.append(f"{iid}: agent 异常 {type(e).__name__}")

        _changed = agent_changed_files(work)   # ★ 必须在打官方 test_patch 之前取
        if not apply_test_patch(inst, work):
            env_errors += 1
            if len(fails) < 5:
                fails.append(f"{iid}: test_patch 打不上")
            continue

        f2p_ok, f2p_n, f2p_tail = run_tests_detail(py, work, _as_list(inst.get("FAIL_TO_PASS")))
        p2p_ok, p2p_n, p2p_tail = run_tests_detail(py, work, _as_list(inst.get("PASS_TO_PASS")))
        evaluated += 1
        # 官方判定：F2P 全过 且 P2P 全过；★ V314 另加反巧合通过——agent 必须改过非测试源码
        _tests_pass = bool(f2p_n) and f2p_ok == f2p_n and p2p_ok == p2p_n
        if _tests_pass and not touched_source(_changed):
            suspects += 1
            if len(fails) < 5:
                fails.append(f"{iid}: ⚠️疑似巧合通过（测试全过但 agent 未改任何非测试源码，"
                             f"改动清单={_changed[:4] or '空'}）——公开审计显示榜单条目存在此类评测有效性问题，"
                             f"本 harness 不计分")
        elif _tests_pass:
            resolved += 1
        elif len(fails) < 5:
            _tail = (f2p_tail if f2p_ok < f2p_n else p2p_tail) or ""
            fails.append(f"{iid}: F2P {f2p_ok}/{f2p_n}, P2P {p2p_ok}/{p2p_n}"
                         + (f" ｜首个失败输出尾部: {_tail}" if _tail else ""))
        _EXP.record_outcome(bench_key, str(inst.get("problem_statement", ""))[:300],
                            bool(_tests_pass and touched_source(_changed)),
                            detail=(f"改了 {len(_changed)} 个文件并通过 F2P+P2P"
                                    if _tests_pass else f"F2P {f2p_ok}/{f2p_n}"))

    if evaluated == 0:
        # ★ V311：不再吞掉真实错误——skip 分支也带 fails/breakdown。
        # 逐条看错误对症：clone 失败=网络/镜像问题（可换镜像重试）；
        # pip/构建失败=Python 版本/构建链问题（老实例本机装不起来是正常的）。
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": (f"[{display}] " + (
                    f"熔断：前 {attempted} 个实例环境都搭不起来（env_error），判定本机跑不了 "
                    f"SWE-bench，提前跳过——省时间（想跑更多再排障可调 HASHMM_SWE_EARLY_ABORT）。"
                    if _aborted_early else
                    f"尝试 {attempted} 个实例全部环境搭建失败（配额 {limit}，候选池 {len(pool)}）"
                    f"——真实错误已列在失败样例。") +
                    f"clone 失败=网络/镜像；pip 失败=Python版本/构建链。"
                    f"长期方案是换支持 Docker 的机器跑官方 harness。"),
                "breakdown": {"环境搭不起来": str(env_errors), "尝试实例数": str(attempted),
                              "提前熔断": "是" if _aborted_early else "否",
                              "判分口径": "未评测（全部 env_error，分数不适用）"},
                "fails": fails[:5]}
    return {
        "kind": "official", "skip": False,
        "passed": resolved, "total": evaluated,
        "score_pct": round(100.0 * resolved / evaluated, 1),
        "comparable": False,
        "detail": f"官方数据集（{display} 评上 {evaluated} 例 / 尝试 {attempted} 例，"
                  f"env_error 自动回填，**本机执行·非官方Docker口径**）",
        "breakdown": {"已评测": str(evaluated), "环境搭不起来(已回填补位·不占名额)": str(env_errors),
                      "尝试实例数": f"{attempted}（配额 {limit} × 候选池 {len(pool)}）",
                      "判分口径": "F2P全过+P2P全过(官方规则)+反巧合通过(须改非测试源码)·本机环境",
                      "疑似巧合通过(不计分)": str(suspects),
                      "环境策略": f"conda 按实例年代选 Python（≥{_MIN_YEAR} 年实例优先；老实例本机构建链装不起来）",
                      "步数预算": f"{_ITERS} 步 / {_TOOLCALLS} 次工具 / {_EXECS} 次执行",
                      **_fmt_bd(resolved, evaluated, bench_key)},
        "fails": fails[:5],
    }

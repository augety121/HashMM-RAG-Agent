"""Terminal-bench —— **无 Docker 的本机模式**（V306）。

**关键洞察**：Terminal-bench 的**判分是官方 pytest 脚本**（`tests/test_outputs.py`），
Docker 只是用来**搭一个干净的环境**。所以在你这台没有 Docker 的 AutoDL 容器上，我可以：

    在**临时目录**里当作任务工作区 → 把任务的初始文件铺进去 → 让 agent 用 shell 干活
    → **跑官方 pytest 判分脚本** → 得分

判分口径 = **官方 pytest**（不打折）；环境口径 ≠ 官方 Docker 镜像 → 所以标为
**"官方任务+官方判分·本机环境(非官方Docker隔离)"**。

**保护你的服务器（三道闸）**：
  1) 只跑 Dockerfile 基于通用 python/ubuntu 基础镜像、且**不需要 apt 装系统包**的任务
     （需要特殊系统依赖的任务本机跑不了 → 如实跳过，不硬跑）；
  2) **静态危险命令过滤**：任务里出现 `rm -rf /`、`apt install`、`systemctl`、`mkfs` 等一律跳过；
  3) 每题在**独立临时目录**里跑，agent 的 run_shell 本身已 cwd 到会话工作区。

依赖：只要 `install.sh terminal` clone 了仓库即可（不需要 Docker、不需要 tb venv）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_DANGER = re.compile(
    r"rm\s+-rf\s+/(?!tmp|app|workdir)|mkfs|shutdown\b|reboot\b|"
    r">\s*/dev/sd|dd\s+if=.*of=/dev/|/etc/(passwd|shadow|sudoers)",
    re.IGNORECASE,
)
# V312 说明：apt install / useradd / passwd / systemctl 从"危险"里移出——它们不是破坏性
# 命令，是环境需求的代理信号。此前把它们当危险一票否决，是 241 题只剩 3 题可跑的元凶之一。
# systemctl 挪进"特殊环境"（容器里没 systemd，跑了也白跑）；apt 变成【能力】：见 apt_ok()。

# 需要特殊系统环境的任务（本机搭不起来 → 如实跳过，不硬跑给假 0 分）。
# apt 依赖单独判：apt_ok() 为真（root + 有 apt-get）时这些任务也能跑，
# 起跑前把 Dockerfile 里的 apt 包装进系统（AutoDL 容器本身就是一次性环境）。
_NEEDS_SPECIAL_ENV = re.compile(
    r"nvidia|cuda|lean4|\bcoq\b|haskell|FROM\s+rust|FROM\s+golang|FROM\s+node|openjdk|kernel|systemctl",
    re.IGNORECASE,
)
_NEEDS_APT = re.compile(r"apt(-get)?\s+install|\byum\s+install", re.IGNORECASE)


def apt_ok() -> bool:
    """本机能否用 apt 现装系统包（HASHMM_TERMINAL_APT: 1 强开 / 0 强关 / 默认 auto）。

    auto = root 且有 apt-get（AutoDL 容器满足）。开了之后，165 个"要 apt 装包"的
    官方任务不再一票否决——可跑池从个位数扩到几十题，样本量才谈得上可比。
    """
    v = os.environ.get("HASHMM_TERMINAL_APT", "auto").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    try:
        return os.geteuid() == 0 and shutil.which("apt-get") is not None
    except Exception:  # noqa: BLE001
        return False


def _apt_pkgs(dockerfile_text: str) -> list[str]:
    """从 Dockerfile 解析 apt-get install 的包名（合并续行、剥 flag/变量/链式命令）。"""
    text = re.sub(r"\\\s*\n", " ", dockerfile_text or "")   # 续行合并
    pkgs: list[str] = []
    for m in re.finditer(r"apt(?:-get)?\s+install\s+([^\n&;|]+)", text, re.IGNORECASE):
        for tok in m.group(1).split():
            if tok.startswith("-") or tok.startswith("$"):
                continue
            if tok in ("install", "apt", "apt-get", "sudo"):
                continue
            base = tok.split("=")[0]          # 剥版本钉（libpq-dev=1.2.3 → libpq-dev）
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._:\-]*", base):
                pkgs.append(base)
    return list(dict.fromkeys(pkgs))


_APT_DONE: set[str] = set()


def _ensure_apt(pkgs: list[str]) -> tuple[bool, str]:
    """把任务需要的系统包装进本机（幂等；已装过的进程内跳过）。"""
    todo = [p for p in pkgs if p not in _APT_DONE]
    if not todo:
        return True, "apt 依赖已就绪"
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    p = subprocess.run(["apt-get", "install", "-y", "-qq", *todo],
                       capture_output=True, text=True, timeout=900, env=env)
    if p.returncode != 0:
        p2 = subprocess.run(["apt-get", "update", "-qq"], capture_output=True,
                            text=True, timeout=600, env=env)
        if p2.returncode == 0:
            p = subprocess.run(["apt-get", "install", "-y", "-qq", *todo],
                               capture_output=True, text=True, timeout=900, env=env)
    if p.returncode == 0:
        _APT_DONE.update(todo)
        return True, f"apt 已装 {', '.join(todo[:6])}"
    tail = ((p.stderr or p.stdout or "")[-200:]).strip()
    return False, f"apt 装不上 {', '.join(todo[:4])}：{tail}"


from . import experience as _EXP
from ._paths import bench_home
from .sample_stats import format_breakdown as _fmt_bd  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks

# ★ V310 步数预算（对标大厂 harness：终端类任务通常给 50~200 步）。可用环境变量调。
_ITERS = int(os.environ.get("HASHMM_TERMINAL_MAX_ITERS", "60"))
_TOOLCALLS = int(os.environ.get("HASHMM_TERMINAL_MAX_TOOLCALLS", "150"))
_EXECS = int(os.environ.get("HASHMM_TERMINAL_MAX_EXECS", "40"))


def tasks_dir() -> Path:
    return bench_home() / "terminal-bench" / "original-tasks"


def tasks_dir_v2() -> Path:
    return bench_home() / "terminal-bench-2" / "tasks"


def active_task_set() -> tuple[str, Path]:
    """当前使用的任务集（V314 双集）：HASHMM_TERMINAL_SET=2|original|auto（默认 auto）。

    注意：这里是无 Docker 的本机诊断 runner。即使任务来自官方仓库，也不等同于
    Terminal-Bench 1.0 Docker 榜或基于 Harbor 的 2.x 榜，因此结果一律标记不可横向比较。
    """
    want = os.environ.get("HASHMM_TERMINAL_SET", "auto").strip().lower()
    v2 = tasks_dir_v2()
    v2_ok = v2.is_dir() and any(v2.glob("*/task.yaml"))
    if want == "2":
        return "2.x", v2
    if want == "original":
        return "original", tasks_dir()
    return ("2.x", v2) if v2_ok else ("original", tasks_dir())


def detect() -> dict:
    set_name, d = active_task_set()
    n = len(list(d.glob("*/task.yaml"))) if d.is_dir() else 0
    n1 = len(list(tasks_dir().glob("*/task.yaml"))) if tasks_dir().is_dir() else 0
    n2 = len(list(tasks_dir_v2().glob("*/task.yaml"))) if tasks_dir_v2().is_dir() else 0
    return {"installed": n > 0, "n_tasks": n, "task_set": set_name,
            "n_original": n1, "n_v2": n2,
            "hint": "" if n else ("未装 Terminal-bench 本机诊断任务：benchmarks/install.sh terminal"
                                  " 或 install.sh terminal2")}


def _read_instruction(task_yaml: Path) -> str:
    """从 task.yaml 里抽 instruction（不引入 yaml 依赖，纯文本解析）。"""
    try:
        text = task_yaml.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"^instruction:\s*\|-?\s*\n((?:[ \t]+.*\n?)+)", text, re.MULTILINE)
    if not m:
        m = re.search(r"^instruction:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""
    lines = [re.sub(r"^\s{2}", "", ln) for ln in m.group(1).splitlines()]
    return "\n".join(lines).strip()


def is_runnable_locally(tdir: Path, allow_apt: bool | None = None) -> bool:
    """只跑本机能起环境、且不含破坏性命令的任务。allow_apt=None 时按 apt_ok() 判。"""
    df = tdir / "Dockerfile"
    blob = ""
    for f in (df, tdir / "task.yaml"):
        if f.exists():
            try:
                blob += f.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                pass
    if not blob:
        return False
    if _DANGER.search(blob):
        return False
    if _NEEDS_SPECIAL_ENV.search(blob):
        return False        # 需要特殊系统环境 → 本机搭不起来，如实跳过
    if _NEEDS_APT.search(blob):
        ok_apt = apt_ok() if allow_apt is None else allow_apt
        if not ok_apt:
            return False    # 没有 apt 能力 → 如实跳过（不是危险，是环境不满足）
    return (tdir / "tests").is_dir()


def load_tasks(limit: int = 10) -> list[dict]:
    tasks, _stats = select_tasks(limit)
    return tasks


def select_tasks(limit: int = 10) -> tuple[list[dict], dict]:
    """选出本机可跑任务 + 全池统计（供报告展示"为什么只有 N 题可跑"）。"""
    set_name, d = active_task_set()
    stats = {"total": 0, "runnable": 0, "runnable_pip_only": 0, "runnable_with_apt": 0,
             "apt_enabled": apt_ok(), "task_set": set_name}
    if not d.is_dir():
        return [], stats
    out: list[dict] = []
    _apt = stats["apt_enabled"]
    for tdir in sorted(d.iterdir()):
        if not (tdir / "task.yaml").exists():
            continue
        stats["total"] += 1
        if not is_runnable_locally(tdir, allow_apt=_apt):
            continue
        instr = _read_instruction(tdir / "task.yaml")
        if not instr:
            continue
        stats["runnable"] += 1
        blob = ""
        df = tdir / "Dockerfile"
        if df.exists():
            try:
                blob = df.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                pass
        pkgs = _apt_pkgs(blob) if _NEEDS_APT.search(blob or "") else []
        if pkgs:
            stats["runnable_with_apt"] += 1
        else:
            stats["runnable_pip_only"] += 1
        if not limit or len(out) < limit:
            out.append({"id": tdir.name, "dir": tdir, "instruction": instr, "apt_pkgs": pkgs})
    return out, stats


def app_path() -> Path:
    """官方任务硬编码的工作目录（默认 /app；测试可用 HASHMM_TERMINAL_APP_PATH 指到临时路径）。"""
    return Path(os.environ.get("HASHMM_TERMINAL_APP_PATH", "/app"))


def _is_dir_mapping(path: Path) -> bool:
    """True for a POSIX symlink or a Windows directory junction."""
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(callable(isjunction) and isjunction(path))


def _remove_dir_mapping(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif _is_dir_mapping(path):
        path.rmdir()
    else:
        raise OSError(f"{path} 不是目录映射")


def _create_dir_mapping(work: Path, app: Path) -> str:
    """Create a directory mapping, falling back to an unprivileged NT junction."""
    try:
        os.symlink(str(work), str(app), target_is_directory=True)
        return "软链"
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise
    # Windows without Developer Mode cannot create symlinks, while a local
    # directory junction provides the same /app mapping semantics without admin.
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(app), str(work)],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0 or not app.exists():
        raise OSError(f"Windows 目录联接失败：{(proc.stderr or proc.stdout).strip()[:200]}")
    return "目录联接"


def map_app(work: Path) -> tuple[bool, str, "object"]:
    """把 /app 映射到本题工作区。返回 (成功?, 模式说明, restore 函数)。

    ★ V312 修 Terminal 恒 0 的又一真凶：V309 垫片只处理了"/app 不存在"和"/app 是
    有效软链"两种状态。用户现场的失败（FileNotFoundError: '/app/results.txt'）说明
    垫片没生效——因为还有两种状态没覆盖：
      · /app 是【悬空软链】（上次运行被硬杀，链还在但指向已删除的临时目录）：
        Path.exists() 对悬空链返回 False → os.symlink 抛 FileExistsError 被吞 →
        垫片从此永久失效，之后每次运行全军覆没。
      · /app 是【真实目录】：两个分支都不命中，垫片静默放弃，而官方测试仍然
        硬查 /app/xx → agent 做得再对也是 0 分。
    现在五种状态全覆盖：缺失→建链；有效链/悬空链→重链；空真目录→删掉建链
    （restore 时原样建回空目录）；非空真目录→改名备份建链（restore 时改回）。
    """
    app = app_path()
    def _noop():
        return None
    try:
        if os.path.lexists(app):                     # 链（含悬空）或真实存在
            if _is_dir_mapping(app):
                _remove_dir_mapping(app)             # 有效链/悬空链/NT junction 统一重建
                kind = _create_dir_mapping(work, app)
                return True, f"重建{kind}（含悬空链修复）", lambda: _remove_dir_mapping(app)
            if app.is_dir():
                if not any(app.iterdir()):           # 空真目录：替换，restore 原样建回
                    app.rmdir()
                    kind = _create_dir_mapping(work, app)
                    def _restore_empty():
                        _remove_dir_mapping(app)
                        app.mkdir(exist_ok=True)
                    return True, f"空目录替换为{kind}", _restore_empty
                bak = app.parent / (app.name + f".hashmm-bak-{os.getpid()}")
                os.rename(app, bak)                  # 非空真目录：改名备份（挂载点会失败→兜底）
                kind = _create_dir_mapping(work, app)
                def _restore_bak():
                    _remove_dir_mapping(app)
                    os.rename(bak, app)
                return True, f"非空目录已改名备份并建{kind}", _restore_bak
            return False, f"{app} 是文件/特殊节点，无法映射", _noop
        kind = _create_dir_mapping(work, app)
        return True, f"新建{kind}", lambda: _remove_dir_mapping(app)
    except Exception as e:  # noqa: BLE001
        return False, f"映射失败：{type(e).__name__}: {e}", _noop


def tests_reference_app(tdir: Path) -> bool:
    """官方判分脚本是否硬编码引用 /app 路径（是→映射失败时该题环境不满足，应剔除而非记 0）。"""
    tests = tdir / "tests"
    if not tests.is_dir():
        return False
    # Path('/app') becomes '\\app' on Windows, but official tests always embed
    # the POSIX container path '/app'. Check both representations.
    needles = {str(app_path()), "/app"}
    for f in tests.rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if any(needle in text for needle in needles):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def run_official_tests(work: Path, tdir: Path) -> tuple[bool, str]:
    """跑官方 pytest 判分脚本（判分口径不打折）。"""
    tests_src = tdir / "tests"
    if not tests_src.is_dir():
        return False, "无官方测试"
    tests_dst = work / "tests"
    if tests_dst.exists():
        shutil.rmtree(tests_dst, ignore_errors=True)
    shutil.copytree(tests_src, tests_dst)
    try:
        p = subprocess.run(
            ["python3", "-m", "pytest", "-q", "--no-header", str(tests_dst)],
            cwd=str(work), capture_output=True, text=True, timeout=300)
        return p.returncode == 0, (p.stdout or p.stderr)[-800:]   # V311: 160→800，看到具体断言
    except subprocess.TimeoutExpired:
        return False, "测试超时"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def run(adapter, *, limit: int = 8) -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit("terminal", limit)
    tasks, pool = select_tasks(limit)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": f"{det['n_tasks']} 个官方任务里，没有本机能安全起环境的"
                          f"（apt 能力={'开' if pool.get('apt_enabled') else '关'}；"
                          f"其余需特殊镜像/工具链）。这类任务只能用 Docker 跑。"}

    from hashmm.api.database import CONV_FILES_ROOT
    from hashmm.tools import agent_bench as AB

    passed = 0
    env_skips: list[str] = []   # 环境不满足（/app 映射失败、apt 装不上）→ 剔除分母，不记 0
    fails: list[str] = []
    for i, t in enumerate(tasks):
        conv_id = f"tb-{t['id']}"[:70]
        work = Path(CONV_FILES_ROOT) / conv_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        # 铺任务初始文件（除 tests/solution 外的资源，如 p.npy / 输入数据）
        for f in t["dir"].iterdir():
            if f.name in ("tests", "solution.sh", "Dockerfile", "docker-compose.yaml", "task.yaml", "run-tests.sh"):
                continue
            try:
                (shutil.copytree if f.is_dir() else shutil.copy)(f, work / f.name)
            except Exception:  # noqa: BLE001
                pass
        # tests 目录里常放数据文件（如 p.npy），任务描述里按 /app/xx 引用 → 也铺一份到工作区根
        for f in (t["dir"] / "tests").iterdir():
            if f.suffix in (".npy", ".csv", ".json", ".txt", ".bin") and not f.name.startswith("test_"):
                try:
                    shutil.copy(f, work / f.name)
                except Exception:  # noqa: BLE001
                    pass

        # ★ V312：apt 依赖任务——起跑前把 Dockerfile 声明的系统包装好；装不上按环境剔除。
        if t.get("apt_pkgs"):
            _aok, _anote = _ensure_apt(t["apt_pkgs"])
            if not _aok:
                env_skips.append(f"{t['id']}: {_anote}")
                continue

        # ★ V312：/app 映射（完备状态机，见 map_app 注释——V309 垫片漏了悬空链和真实目录，
        # 用户现场因此全军覆没）。映射失败且官方测试硬编码 /app → 环境不满足，剔除不记 0。
        app_ok, app_mode, app_restore = map_app(work)
        if not app_ok and tests_reference_app(t["dir"]):
            env_skips.append(f"{t['id']}: /app 无法映射（{app_mode}）而判分硬查 /app 路径")
            continue

        _cwd_hint = (f"{app_path()} 已指向你的工作目录，任务里的 {app_path()}/xx 路径可直接使用。" if app_ok
                     else "任务里提到的 /app/ 路径就是你当前的工作目录（用相对路径读写）。")
        q = ("你在一个 Linux 工作区里。用 run_shell / create_file / execute_code 等工具完成下面的任务。"
             f"{_cwd_hint}\n"
             "工作方法（务必遵守）：\n"
             "1. 先 `ls -la` 看清工作区里有什么文件，再 `head`/`cat` 看清输入数据的真实格式"
             "（不要凭任务描述猜格式——真实数据常有边界情况）。\n"
             "2. 写脚本实现，然后**真的运行它**，看输出。\n"
             "3. 运行后必须 `cat` 产出文件，逐条核对是否满足任务里的每一条要求"
             "（字段名、顺序、格式、精度、边界）。\n"
             "4. 不符合就改，改完再跑再看——你有充足的步数预算，宁可多验证几轮，不要早收工。\n"
             "⚠️ 绝不要凭空宣称完成。完成后不需要解释。\n\n"
             f"=== 任务 ===\n{t['instruction'][:8000]}")
        # 第二轮：强制自查（不给测试文件，纯靠任务要求复核 —— 不是作弊，是大厂 harness 的标准做法）
        q2 = ("现在做最终自查：把任务要求逐条列出来，然后用 shell 实际检查你的产出是否满足每一条"
              "（文件是否存在、内容/字段/格式/数值是否正确、边界情况是否处理）。"
              "发现任何不符就立刻修复并重新验证。全部确认无误后回复「已完成」。")
        turns = [q, q2]
        try:
            # ★ V310 修 Terminal-bench 恒 0 的【真正】原因：步数预算。
            # 之前 agent 每题只有 MAX_ITERATIONS=10 步、MAX_EXEC_CALLS=5 次执行 —— 而这类
            # 任务要「读数据→写脚本→跑→报错→改→再跑→验证」，10 步远远不够，agent 在做完
            # 之前就被强制截停 → 产物是半成品 → 官方测试跑得起来但断言失败（这就是为什么
            # V309 修完目录/权限后，失败信息从「文件不存在」变成了 AssertionError）。
            # 大厂 harness 给终端类任务的预算是 50~200 步，这里给 60 步 / 150 次工具 / 40 次执行。
            task = AB.Task(id=conv_id, category="terminal", turns=turns, requires=set(),
                           scorers=[AB.answer_nonempty(min_len=1)], max_seconds=1800,
                           max_iterations=_ITERS, max_tool_calls=_TOOLCALLS,
                           max_exec_calls=_EXECS)
            # ★ V309 修恒 0 分三元凶：conv_id 对齐（agent 与判分同目录）、preserve_workspace
            # （不删预铺任务文件、不在判分前清场）、作用域 bypass（run_shell 是 SYSTEM 级，
            # 评测无人点批准会被权限卡死——这不是模型差）。提权仅限本次评测 user，跑完自动撤销。
            # ★ V315 经验闭环：同类任务带上以往【官方判分认定成功】的做法（Hermes 方向）。
            # HASHMM_BENCH_EXPERIENCE=0 时 hint_kwargs 返回 {} → 与旧行为零差异。
            AB.run_task(task, adapter.llm_fn, conv_id=conv_id,
                        preserve_workspace=True, permission_mode="bypass",
                        **_EXP.hint_kwargs("terminal"))
        except Exception:  # noqa: BLE001
            pass
        ok = False
        why = ""
        try:
            ok, why = run_official_tests(work, t["dir"])
        finally:
            if app_ok:
                try:
                    app_restore()
                except Exception:  # noqa: BLE001
                    pass
        if ok:
            passed += 1
        elif len(fails) < 5:
            fails.append(f"{t['id']}: {why[:400]}")   # V311: 60→400，agent 到底哪错了要看得见
        # 判分之后才知道真相 → 用官方判分结果回录经验（reward=1.0/0.0）
        _EXP.record_outcome("terminal", t["instruction"], ok,
                            detail=("通过官方 pytest" if ok else why[:160]))

    total = len(tasks) - len(env_skips)
    if total <= 0:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "选出的任务全部因环境不满足被剔除（/app 映射失败或 apt 装不上）——"
                          "详见失败样例，修好环境后重跑。",
                "breakdown": {"环境剔除": str(len(env_skips))},
                "fails": env_skips[:5]}
    _apt_note = ("开（root+apt-get，可跑池含 apt 依赖任务）" if pool.get("apt_enabled")
                 else "关（HASHMM_TERMINAL_APT=1 可强开；apt 依赖任务已跳过）")
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "comparable": False,
        "detail": f"官方任务（Terminal-bench 评 {total} 题，**官方 pytest 判分 · 本机环境·非官方Docker隔离**）",
        "breakdown": {"任务集": f"{det.get('task_set', 'original')}（original 装 {det.get('n_original', 0)} / 2.x 装 {det.get('n_v2', 0)}；HASHMM_TERMINAL_SET 可切）",
                      "官方任务总数": str(det["n_tasks"]),
                      "本机可跑池": f"{pool.get('runnable', 0)}"
                                   f"（仅pip {pool.get('runnable_pip_only', 0)} + "
                                   f"apt依赖 {pool.get('runnable_with_apt', 0)}）",
                      "apt 能力": _apt_note,
                      "环境剔除(不记0分)": str(len(env_skips)) if env_skips else "0",
                      "判分口径": "官方 pytest（不打折）",
                      "步数预算": f"{_ITERS} 步 / {_TOOLCALLS} 次工具 / {_EXECS} 次执行（V310：此前只有 10 步，agent 做到一半被截停）",
                      **_fmt_bd(passed, total, "terminal")},
        "fails": (fails + [f"[环境剔除] {s}" for s in env_skips])[:5],
    }

"""Kotlin 编码基准（HumanEval-Kotlin）—— V306。

★ 不需要 Docker。需要 **JDK + kotlinc** —— 你的服务器没有，所以 `install.sh kotlin` 会把
**独立的 JDK 和 kotlinc 下载到 `$HASHMM_BENCH_HOME/` 目录里**（只加文件、不装系统包、不改 PATH、
不碰你的 conda 环境），本模块用**绝对路径**调用它们。

数据：`HumanEval_kotlin_v1.1.jsonl`（amazon-science/mxeval，161 题，GitHub 直连），
每题带 `prompt`（函数签名+文档）和 `test`（可执行的 Kotlin main 测试）。

判分：**真 pass@1** —— 拼 `prompt + 模型补全 + test` → kotlinc 真编译 → 真运行 →
不抛异常即通过。不是"看起来像 Kotlin 就给分"，是真的跑起来。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def _sh(cmd: list[str], cwd: str | None = None, env: dict | None = None,
        timeout: int = 180) -> tuple[int, str, str]:
    try:
        e = {**os.environ, **(env or {})}
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as ex:  # noqa: BLE001
        return 1, "", f"{type(ex).__name__}: {ex}"


def java_home() -> Path | None:
    """独立 JDK（install.sh 下载到 bench home；也接受系统已有的 java）。"""
    for c in bench_home().glob("jdk*"):
        if (c / "bin" / "java").exists():
            return c
    import shutil
    j = shutil.which("java")
    if j:
        return Path(j).parent.parent
    return None


def kotlinc_bin() -> Path | None:
    for c in (bench_home() / "kotlinc" / "bin" / "kotlinc",
              bench_home() / "kotlinc" / "bin" / "kotlinc-jvm"):
        if c.exists():
            return c
    return None


def _data_file() -> Path | None:
    root = bench_home() / "mxeval"
    if not root.is_dir():
        return None
    hits = list(root.rglob("HumanEval_kotlin*.jsonl"))
    return hits[0] if hits else None


def detect() -> dict:
    jh, kc, df = java_home(), kotlinc_bin(), _data_file()
    ok = bool(jh and kc and df)
    if ok:
        hint = ""
    elif not df:
        hint = "未装 Kotlin 数据集：运行 benchmarks/install.sh kotlin"
    elif not jh or not kc:
        hint = "未装 JDK/kotlinc：运行 benchmarks/install.sh kotlin（会下载到隔离目录，不改你的系统环境）"
    else:
        hint = ""
    return {"installed": ok, "jdk": bool(jh), "kotlinc": bool(kc), "data": bool(df), "hint": hint}


def load_tasks(limit: int = 30) -> list[dict]:
    f = _data_file()
    if not f:
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if t.get("prompt") and t.get("test"):
            out.append(t)
    return out[:limit] if limit else out


def extract_code(raw: str) -> str:
    """从模型输出里抽出代码。加固版：容忍"解释文字+代码"、未闭合围栏、代码块前后噪声。
    ★ 修测试侧 bug：旧版只认成对围栏，模型若在代码前加一句"Here is the solution:"（无围栏），
    整段（含解释）会被当代码执行 → 语法错误 → 明明写对也判 0。现在：①优先取成对围栏内部；
    ②只有开头围栏没闭合（输出被截断）→ 取围栏之后全部；③模型加了前言 → 从第一行
    def/class/import/from/@ 开始截取。注意：截断到一半没函数体的情况仍会判错——那是模型
    输出被截断（模型/端点侧），测试无法凭空补出函数体。"""
    s = str(raw or "")
    m = re.search(r"```(?:[a-zA-Z0-9_+-]*)?\s*\n([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    m = re.search(r"```(?:[a-zA-Z0-9_+-]*)?\s*\n([\s\S]*)$", s)   # 未闭合围栏
    if m:
        s = m.group(1)
    lines = s.splitlines()
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("def ", "async def ", "class ", "import ", "from ", "@", "fun ")):
            return "\n".join(lines[i:]).strip()
    return s.strip()


def compile_and_run(prompt: str, completion: str, test: str, workdir: Path) -> tuple[bool, str]:
    """拼完整程序 → kotlinc 编译 → java 运行。返回 (通过, 说明)。"""
    kc, jh = kotlinc_bin(), java_home()
    if not kc or not jh:
        return False, "kotlinc/JDK 缺失"
    src = workdir / "Solution.kt"
    # 模型如果重复了签名，就直接用它的完整代码；否则 prompt + 补全
    c = (completion or "").strip()
    # 模型给了完整代码（自带 fun 签名）→ 直接用；只给了函数体 → 拼在 prompt 后面
    body = c if c.startswith(("fun ", "import ", "/*", "//")) else prompt + "\n" + c
    src.write_text(body + "\n\n" + test, encoding="utf-8")

    jar = workdir / "out.jar"
    env = {"JAVA_HOME": str(jh), "PATH": f"{jh}/bin:{os.environ.get('PATH', '')}"}
    rc, _, err = _sh([str(kc), "-include-runtime", "-d", str(jar), str(src),
                      "-nowarn", "-jvm-target", "17"], cwd=str(workdir), env=env, timeout=240)
    if rc != 0:
        return False, f"编译失败：{err.strip()[:120]}"
    rc, out, err = _sh([str(jh / "bin" / "java"), "-jar", str(jar)],
                       cwd=str(workdir), env=env, timeout=60)
    if rc != 0:
        return False, f"测试失败：{(err or out).strip()[:120]}"
    return True, "通过"


def run(adapter, *, limit: int = 30) -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit("kotlin", limit)   # 默认 standard(50)，仍尊重 HASHMM_KOTLIN_LIMIT
    tasks = load_tasks(limit)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "Kotlin 数据集已存在但解析不出任务"}

    passed = 0
    fails: list[str] = []
    cases: list[dict] = []
    reasons: dict = {"编译失败": 0, "测试失败": 0, "其他": 0}
    with tempfile.TemporaryDirectory(prefix="kotlin_bench_") as td:
        root = Path(td)

        # V324 并发：每题独立（LLM + 独立目录 kotlinc 编译），并行跑。每题用自己的 wd。
        def _one(iv):
            i, t = iv
            wd = root / f"t{i}"
            wd.mkdir(parents=True, exist_ok=True)
            p = ("补全下面这个 Kotlin 函数的实现（只输出完整的 Kotlin 代码，不要解释、不要 markdown 围栏）：\n\n"
                 + t["prompt"])
            try:
                raw = adapter.answer(p)
            except Exception:  # noqa: BLE001
                return {"i": i, "t": t, "err": True}
            ok, why = compile_and_run(t["prompt"], extract_code(raw), t["test"], wd)
            return {"i": i, "t": t, "ok": ok, "why": why}

        from .parallel import run_parallel
        results = run_parallel(list(enumerate(tasks)), _one)
        for r in results:
            i, t = r["i"], r["t"]
            if r.get("err") or r.get("_error"):
                reasons["其他"] += 1
                continue
            ok, why = bool(r.get("ok")), r.get("why", "")
            if ok:
                passed += 1
            else:
                key = "编译失败" if why.startswith("编译") else ("测试失败" if why.startswith("测试") else "其他")
                reasons[key] += 1
                if len(fails) < 5:
                    fails.append(f"{t.get('task_id', i)}: {why}")
            cases.append({"id": str(t.get("task_id", i))[:24], "ok": ok,
                          "entry": str(t.get("entry_point", ""))[:30],
                          "why": ("" if ok else why[:80])})

    total = len(tasks)
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "detail": f"官方数据集（HumanEval-Kotlin 前 {total} 题，**真 kotlinc 编译 + 真跑测试** 判 pass@1）",
        "breakdown": {**{k: str(v) for k, v in reasons.items() if v},
                      "判分口径": "真编译真运行（非静态检查）"},
        "fails": fails[:5],
        "cases": cases,
    }

"""HumanEval + MBPP —— 纯 Python 代码基准，V306。

★★ 这俩是**最省事**的可对标基准：纯 Python，**不需要 Docker、不需要任何编译器**，
你服务器的 python 直接就能跑。数据都在 GitHub（openai/human-eval、google-research/mbpp）。

判分：**真执行** —— 把模型补全的代码 + 官方测试用 `exec` 在**子进程沙箱**里跑，
不抛异常/断言全过即通过。这是 HumanEval/MBPP 的标准 pass@1 口径。

安全：每题在**独立子进程**里跑（设超时），代码里做危险调用静态过滤（import os/subprocess/
删文件/联网 等一律不跑该题），保护你的服务器。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ._paths import bench_home

# 生成代码里出现这些就不执行该题（防模型写出删文件/联网/起进程的代码在你机器上跑）
_UNSAFE = re.compile(
    r"\b(import\s+(os|sys|subprocess|shutil|socket|requests|urllib)|"
    r"__import__|eval\s*\(|exec\s*\(|open\s*\(|"
    r"os\.(system|remove|rmdir|unlink|popen)|subprocess|socket\.|"
    r"rmtree|\brmdir\b)",
    re.IGNORECASE,
)


def humaneval_file() -> Path | None:
    root = bench_home() / "human-eval"
    for c in (root / "data" / "HumanEval.jsonl.gz", root / "data" / "HumanEval.jsonl"):
        if c.exists():
            return c
    hits = list(root.rglob("HumanEval.jsonl*")) if root.is_dir() else []
    return hits[0] if hits else None


def mbpp_file() -> Path | None:
    root = bench_home() / "google-research"
    hits = list(root.rglob("mbpp.jsonl")) if root.is_dir() else []
    return hits[0] if hits else None


def detect() -> dict:
    he, mb = humaneval_file(), mbpp_file()
    return {"installed": bool(he or mb), "humaneval": bool(he), "mbpp": bool(mb),
            "hint": "" if (he or mb) else "未装数据：运行 benchmarks/install.sh humaneval（纯 Python，不需要 Docker/编译器）"}


def _load_humaneval(limit: int) -> list[dict]:
    f = humaneval_file()
    if not f:
        return []
    opener = gzip.open if f.suffix == ".gz" else open
    out = []
    with opener(f, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                d = json.loads(line)
                out.append({"id": d["task_id"], "prompt": d["prompt"],
                            "test": d["test"], "entry": d["entry_point"], "kind": "humaneval"})
            if limit and len(out) >= limit:
                break
    return out


def _load_mbpp(limit: int) -> list[dict]:
    f = mbpp_file()
    if not f:
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append({"id": f"mbpp/{d['task_id']}", "text": d["text"],
                    "test_list": d.get("test_list", []),
                    "setup": d.get("test_setup_code", ""), "kind": "mbpp"})
        if limit and len(out) >= limit:
            break
    return out


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


def _run_program(program: str, timeout: int = 15) -> tuple[bool, str]:
    """在独立子进程里执行完整程序（补全+测试）。返回 (通过, 说明)。"""
    if _UNSAFE.search(program):
        return False, "含不安全调用，跳过执行（保护服务器）"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(program)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        ok = p.returncode == 0
        return ok, ("通过" if ok else (p.stderr or p.stdout)[-140:])
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            os.unlink(path)
        except Exception:  # noqa: BLE001
            pass



def _strip_fences(text: str) -> str:
    """去掉模型输出里的 markdown 代码围栏 ```lang ... ```（很多模型无视'不要markdown'照样包）。
    ★ 修 bug：此前不清洗，围栏行 ```python 会被当成代码执行 → 语法错误 → 明明写对也判 0，压低分数。
    仅当存在成对围栏时取围栏内内容；否则原样返回。"""
    if not text:
        return text
    # 优先取第一个 ```lang\n...\n``` 块
    m = re.search(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    # 没有成对围栏但有残留的 ``` 行 → 逐行删掉纯围栏行
    if "```" in text:
        return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```"))
    return text


def _build_humaneval_program(task: dict, completion: str) -> str:
    c = _strip_fences(completion)   # ★ 先剥 markdown 围栏
    prompt = task["prompt"]
    if c.lstrip().startswith(("def ", "import ", "from ", "@")):
        # 模型给了完整函数 —— 但常漏掉 prompt 顶部的 import（如 from typing import List）。
        # 把 prompt 里 def 之前的 import/前置行补在前面，避免 NameError。
        pre = []
        for ln in prompt.splitlines():
            s = ln.strip()
            if s.startswith(("import ", "from ")):
                pre.append(ln)
            elif s.startswith("def "):
                break
        body = ("\n".join(pre) + "\n\n" + c) if pre else c
    else:
        body = prompt + "\n" + c   # 只给了函数体 → 接在 prompt 后
    return f"{body}\n\n{task['test']}\n\ncheck({task['entry']})\n"


def _build_mbpp_program(task: dict, completion: str) -> str:
    parts = [task.get("setup", ""), _strip_fences(completion), ""]   # ★ 先剥 markdown 围栏
    parts += task.get("test_list", [])
    return "\n".join(parts) + "\n"


def run(adapter, *, limit: int = 20, which: str = "both") -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit("humaneval", limit)   # 默认 standard(50)，仍尊重 HASHMM_HUMANEVAL_LIMIT
    which = os.environ.get("HASHMM_CODEBENCH_WHICH", which)
    tasks: list[dict] = []
    if which in ("both", "humaneval"):
        tasks += _load_humaneval(limit)
    if which in ("both", "mbpp"):
        tasks += _load_mbpp(limit)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": "没解析出题目"}

    passed = 0
    cases: list[dict] = []
    fails: list[str] = []
    by_set: dict = {"humaneval": [0, 0], "mbpp": [0, 0]}

    # V324 并发：每题独立（LLM 调用 + 独立子进程执行），并行跑成倍提速。
    def _one(t: dict) -> dict:
        if t["kind"] == "humaneval":
            prompt = ("补全下面的 Python 函数（只输出函数体或完整函数，不要解释、不要 markdown）：\n\n"
                      + t["prompt"])
        else:
            prompt = (f"用 Python 实现：{t['text']}\n"
                      f"要求能通过这些断言：{'; '.join(t['test_list'][:2])}\n"
                      "只输出函数定义，不要解释、不要 markdown。")
        try:
            raw = adapter.answer(prompt)
        except Exception:  # noqa: BLE001
            raw = ""
        code = extract_code(raw)
        program = (_build_humaneval_program(t, code) if t["kind"] == "humaneval"
                   else _build_mbpp_program(t, code))
        ok, why = _run_program(program)
        return {"id": t["id"], "ok": ok, "set": t["kind"], "why": ("" if ok else why[:70])}

    from .parallel import run_parallel
    results = run_parallel(tasks, _one)
    # 主线程按序聚合（worker 不碰共享变量，避免竞争）
    for t, r in zip(tasks, results):
        by_set[t["kind"]][1] += 1
        ok = bool(r.get("ok"))
        if ok:
            passed += 1
            by_set[t["kind"]][0] += 1
        elif len(fails) < 5:
            fails.append(f"{t['id']}: {r.get('why', r.get('_error', ''))[:50]}")
        cases.append({"id": t["id"], "ok": ok, "set": t["kind"], "why": r.get("why", "")})

    total = len(tasks)
    bd = {}
    for k, (o, n) in by_set.items():
        if n:
            bd[k] = f"{o}/{n}"
    bd["判分口径"] = "真执行 pass@1（子进程沙箱）"
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "detail": f"官方数据集（HumanEval/MBPP {total} 题，纯 Python 真执行判 pass@1，无需 Docker）",
        "breakdown": bd,
        "fails": fails[:5],
        "cases": cases,
    }

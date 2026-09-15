#!/usr/bin/env python3
"""fetch_datasets.py — 基准数据集多源下载器（V329：终结"每次都是数据集下载不了"）。

为什么单靠 `datasets.load_dataset` 不行：
  · GitHub Actions 共享 IP 池，匿名打 huggingface.co 常被限流（429/403）；
  · datasets/huggingface_hub 在 import 时就固化 HF_ENDPOINT——跑到一半换端点无效；
  · 任何单一源都可能抽风，用户已经连续三轮被"下载失败"卡住。

本脚本的做法（任一条路通即成功）：
  方法A  datasets.load_dataset            —— happy path，用当前 HF_ENDPOINT 打一枪；
  方法B  huggingface_hub 下 parquet        —— 显式 endpoint= 参数，逐端点尝试；
  方法C  urllib 直连 {endpoint}/resolve    —— 纯标准库 HTTP（+pyarrow 解析），逐端点尝试；
  端点链：$HF_ENDPOINT（若设）→ https://huggingface.co → https://hf-mirror.com；
  鉴权：  HF_TOKEN / HUGGING_FACE_HUB_TOKEN（免费注册即可，带 token 基本不会被限流）；
  校验：  行数 ≥ min_rows、关键字段齐；原子写（tmp→rename，绝不留半截文件）；
  失败：  打印【每一次尝试】的来源与错误的完整报告，退出码 1。

用法：python fetch_datasets.py swebench --out /path/to/BENCH_HOME
可导入：fetch(bench, out_dir, methods=None) —— methods 可注入，供离线单测。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# ── 基准注册表：repo 候选 / split / 输出文件名 / 字段 / 最少行数（防半截数据混进判分）──
REGISTRY: dict[str, dict] = {
    "swebench": {
        "repos": ["princeton-nlp/SWE-bench_Verified"],
        "split": "test",
        "out": "swebench_verified.jsonl",
        "keys": ["instance_id", "repo", "base_commit", "problem_statement",
                 "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"],
        "required_keys": ["instance_id", "problem_statement", "FAIL_TO_PASS", "PASS_TO_PASS"],
        "min_rows": 400,   # 官方 500 题；低于 400 视为坏源
    },
    "swebench_pro": {
        "repos": ["ScaleAI/SWE-bench_Pro", "scaleapi/SWE-bench_Pro"],
        "split": "test",
        "out": "swebench_pro.jsonl",
        # Pro 不是 Verified 的同构数据：官方字段名是小写，并带有 Docker 评测脚本所需元数据。
        # 这些字段不能在缓存前裁掉，否则即使 JSONL 有 731 行也无法按官方口径判分。
        "keys": ["instance_id", "repo", "base_commit", "problem_statement",
                 "patch", "test_patch", "requirements", "interface", "repo_language",
                 "fail_to_pass", "pass_to_pass", "before_repo_set_cmd",
                 "selected_test_files_to_run", "dockerhub_tag", "issue_specificity",
                 "issue_categories"],
        "required_keys": ["instance_id", "problem_statement", "fail_to_pass", "pass_to_pass",
                          "before_repo_set_cmd", "selected_test_files_to_run"],
        "min_rows": 500,   # 公开 split 731 题
    },
}

_OFFICIAL = "https://huggingface.co"
_MIRROR = "https://hf-mirror.com"


def _token() -> str | None:
    return (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip() or None


def _endpoints() -> list[str]:
    eps: list[str] = []
    envep = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    for e in ([envep] if envep else []) + [_OFFICIAL, _MIRROR]:
        if e and e not in eps:
            eps.append(e)
    return eps


def _rows_to_jsonl_atomic(rows: list[dict], spec: dict, out_path: Path) -> int:
    keys = spec["keys"]
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r.get(k) for k in keys if k in r}, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(out_path)          # 原子替换：要么完整文件，要么没有
    return n


def _validate(rows: list[dict], spec: dict) -> None:
    if len(rows) < int(spec["min_rows"]):
        raise ValueError(f"行数 {len(rows)} < 最少 {spec['min_rows']}（坏源/半截数据，拒收）")
    first = rows[0]
    need = [k for k in spec.get("required_keys", ("instance_id", "problem_statement"))
            if k not in first]
    if need:
        raise ValueError(f"首行缺关键字段 {need}（源结构不对，拒收）")


# ─────────────────────────── 方法A：datasets 库（happy path 一枪）───────────────────────────
def via_datasets(spec: dict, endpoint: str, token: str | None) -> list[dict]:
    from datasets import load_dataset  # noqa: PLC0415
    last: Exception | None = None
    for repo in spec["repos"]:
        try:
            ds = load_dataset(repo, split=spec["split"], token=token)
            return [dict(r) for r in ds]
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"datasets 库全部 repo 候选失败：{last!r}")


# ─────────────────────────── parquet 解析（B/C 共用）───────────────────────────
def _read_parquets(paths: list[Path]) -> list[dict]:
    import pyarrow.parquet as pq  # noqa: PLC0415  datasets 的硬依赖，装了 datasets 就有
    rows: list[dict] = []
    for p in paths:
        rows.extend(pq.read_table(p).to_pylist())
    return rows


def _pick_parquet_files(names: list[str], split: str) -> list[str]:
    cand = [n for n in names if n.endswith(".parquet")]
    hit = [n for n in cand if split.lower() in n.lower()]
    return hit or cand


# ─────────────────────────── 方法B：huggingface_hub 显式 endpoint ───────────────────────────
def via_hub_parquet(spec: dict, endpoint: str, token: str | None) -> list[dict]:
    from huggingface_hub import hf_hub_download, list_repo_files  # noqa: PLC0415
    last: Exception | None = None
    for repo in spec["repos"]:
        try:
            names = list_repo_files(repo, repo_type="dataset", endpoint=endpoint, token=token)
            files = _pick_parquet_files(list(names), spec["split"])
            if not files:
                raise FileNotFoundError(f"{repo} 没找到 parquet 文件（列表：{list(names)[:8]}…）")
            local = [Path(hf_hub_download(repo, f, repo_type="dataset",
                                          endpoint=endpoint, token=token)) for f in files]
            return _read_parquets(local)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"hub@{endpoint} 全部 repo 候选失败：{last!r}")


# ─────────────────────────── 方法C：urllib 直连（纯标准库 HTTP）───────────────────────────
def _http_get(url: str, token: str | None, *, tries: int = 3, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hashmm-fetch/1.0"})
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} 重试 {tries} 次仍失败：{last!r}")


def via_urllib_parquet(spec: dict, endpoint: str, token: str | None) -> list[dict]:
    import tempfile  # noqa: PLC0415
    last: Exception | None = None
    for repo in spec["repos"]:
        try:
            meta = json.loads(_http_get(f"{endpoint}/api/datasets/{repo}", token, timeout=60))
            names = [s.get("rfilename", "") for s in (meta.get("siblings") or [])]
            files = _pick_parquet_files(names, spec["split"])
            if not files:
                raise FileNotFoundError(f"{repo} siblings 里没有 parquet（{names[:8]}…）")
            local: list[Path] = []
            with tempfile.TemporaryDirectory() as td:
                for f in files:
                    raw = _http_get(f"{endpoint}/datasets/{repo}/resolve/main/{f}", token)
                    p = Path(td) / Path(f).name
                    p.write_bytes(raw)
                    local.append(p)
                return _read_parquets(local)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"urllib@{endpoint} 全部 repo 候选失败：{last!r}")


# ─────────────────────────── 主流程：方法×端点全排列，任一成即收 ───────────────────────────
def default_methods() -> list[tuple[str, object]]:
    """(标签, callable(spec, endpoint, token)->rows)。A 只打默认端点一枪；B/C 逐端点。"""
    out: list[tuple[str, object]] = [("datasets库(默认端点)",
                                      lambda s, _e, t: via_datasets(s, "", t))]
    for ep in _endpoints():
        out.append((f"hub_parquet@{ep}", lambda s, _e, t, ep=ep: via_hub_parquet(s, ep, t)))
    for ep in _endpoints():
        out.append((f"urllib_parquet@{ep}", lambda s, _e, t, ep=ep: via_urllib_parquet(s, ep, t)))
    return out


def fetch(bench: str, out_dir: str | Path, *, methods: list[tuple[str, object]] | None = None,
          min_rows: int | None = None) -> int:
    if bench not in REGISTRY:
        print(f"!! 未知基准 {bench}（可选：{', '.join(REGISTRY)}）")
        return 2
    spec = dict(REGISTRY[bench])
    if min_rows is not None:
        spec["min_rows"] = min_rows
    out_path = Path(out_dir) / spec["out"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"-- 已存在非空 {out_path}，跳过下载（要强制重下先删掉它）")
        return 0
    tok = _token()
    print(f"== 下载 {bench} → {out_path}")
    print(f"   端点链：{' → '.join(_endpoints())} · token：{'已带' if tok else '匿名（易被限流，建议配 HF_TOKEN）'}")
    attempts: list[tuple[str, str]] = []
    for label, fn in (methods if methods is not None else default_methods()):
        try:
            print(f"→ 尝试 {label} …")
            rows = fn(spec, "", tok)  # type: ignore[operator]
            _validate(rows, spec)
            n = _rows_to_jsonl_atomic(rows, spec, out_path)
            print(f"✅ 成功：{label} → 写出 {n} 条到 {out_path}")
            return 0
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            attempts.append((label, msg))
            print(f"   ✗ {label}：{msg}")
    print("\n!! 全部下载路径失败——逐条尝试报告：")
    for label, msg in attempts:
        print(f"   · {label} → {msg}")
    print("   排查：① 配免费 HF_TOKEN（huggingface.co/settings/tokens，Read 权限）加进 CI Secrets；"
          "② 稍后重跑（限流是暂时的）；③ 手工转 jsonl 放到目标路径。")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="基准数据集多源下载器（官方→镜像→直连，带 HF_TOKEN 与校验）")
    ap.add_argument("bench", choices=sorted(REGISTRY))
    ap.add_argument("--out", required=True, help="输出目录（BENCH_HOME）")
    ap.add_argument("--min-rows", type=int, default=None, help="覆盖最少行数校验（默认取注册表）")
    a = ap.parse_args()
    return fetch(a.bench, a.out, min_rows=a.min_rows)


if __name__ == "__main__":
    sys.exit(main())

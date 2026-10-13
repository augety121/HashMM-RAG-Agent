"""hashmm/training/fetch_public_zh_data.py — 下载公开中文 QA 数据集并转成 golden_cases 格式。

给你的检索策略模型「加量」用。所有数据集均经联网核实真实可下载（见 CHANGELOG-V103.61 的链接表）。
转出的 golden_cases.json 直接喂 build_sft_data.py（含 supporting 段落，配 retrieval 风格教检索）。

支持的数据集（--dataset）：
  cmrc2018      —— 哈工大讯飞 人工标注 中文篇章片段抽取阅读理解（hfl/cmrc2018，可直接 load_dataset）
                   每条：问题 + 答案 + 出处段落(context)。约 1.8 万训练问题。质量高、首选。
  multidoc      —— yuyijiong/Multi-Doc-QA-Chinese 多文档中文QA（需在 HF 同意条款后下载）。
                   每条含参考文档 + 多个无关文档 + 问答，最贴近 RAG「多文档里抽取」场景。cc-by-nc-4.0。
  drcd          —— DRCD 繁体中文阅读理解（可经 load_dataset；若失败请按 README 走 HF 镜像）。

用法（AutoDL，项目根目录；HF 国内慢就先 export HF_ENDPOINT=https://hf-mirror.com）：
    python -m hashmm.training.fetch_public_zh_data --dataset cmrc2018 \
        --out /root/autodl-tmp/data/golden/golden_cmrc2018.json --limit 5000

    # 合并你自己的企业题 + 公开题一起训（推荐：企业题保领域、公开题补量与多样性）
    python -m hashmm.training.merge_golden \
        /root/autodl-tmp/data/golden/golden_cases_corpus.json \
        /root/autodl-tmp/data/golden/golden_cmrc2018.json \
        --out /root/autodl-tmp/data/golden/golden_merged.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _case(idx: int, query: str, answer: str, supporting: str, source: str) -> dict:
    """统一成 golden_cases 格式（与 casegen 产出一致，含 supporting 供 retrieval 风格使用）。"""
    return {
        "id": f"{source}_{idx:06d}",
        "query": (query or "").strip()[:300],
        "category": "factual",
        "must_contain": [],
        "must_cite": True,
        "min_sources": 1,
        "reference_answer": (answer or "").strip()[:500],
        "supporting": [(supporting or "").strip()[:800]] if supporting else [],
        "relevant_docs": [source],
    }


def from_cmrc2018(limit: int) -> list[dict]:
    """hfl/cmrc2018：span 抽取式阅读理解。每条 context(出处段落) + question + answers。"""
    from datasets import load_dataset
    ds = load_dataset("cmrc2018", split="train")
    out = []
    for i, ex in enumerate(ds):
        if limit and len(out) >= limit:
            break
        ans_list = (ex.get("answers") or {}).get("text") or []
        ans = ans_list[0] if ans_list else ""
        q = ex.get("question", "")
        ctx = ex.get("context", "")
        if q and ans:
            out.append(_case(i, q, ans, ctx, "cmrc2018"))
    return out


def from_dureader(limit: int, subset: str = "robust") -> list[dict]:
    """luozhouyang/dureader：百度 DuReader。该仓库带旧式加载脚本(dureader.py)，新版 datasets
    已不支持脚本式数据集，故这里**直接读 HF 自动转换的 Parquet**（绕过脚本），用 pandas 解析。
    子集 robust(6.59万) / checklist(5.41万)。取有答案的样本（is_impossible 为假）。"""
    import pandas as pd
    from huggingface_hub import hf_hub_download, list_repo_files

    repo = "luozhouyang/dureader"
    rev = "refs/convert/parquet"   # HF 自动转换 Parquet 的固定分支
    # 列出该分支下属于本子集、train 划分的 parquet 文件
    try:
        files = list_repo_files(repo, repo_type="dataset", revision=rev)
    except Exception as e:
        raise RuntimeError(f"读取 dureader Parquet 文件列表失败：{e}")
    want = [f for f in files
            if f.endswith(".parquet") and f.startswith(f"{subset}/") and "/train/" in f]
    if not want:
        # 兜底：放宽到该子集下任意 parquet
        want = [f for f in files if f.endswith(".parquet") and f.startswith(f"{subset}/")]
    if not want:
        raise RuntimeError(f"dureader 的 Parquet 分支里没找到子集 {subset} 的文件；可用文件示例：{files[:5]}")

    out: list[dict] = []
    for rel in sorted(want):
        if limit and len(out) >= limit:
            break
        local = hf_hub_download(repo, rel, repo_type="dataset", revision=rev)
        df = pd.read_parquet(local)
        for _, row in df.iterrows():
            if limit and len(out) >= limit:
                break
            ans = row.get("answers")
            # answers 可能是 dict{'text':[...]} 或 直接 list / ndarray
            if isinstance(ans, dict):
                texts = ans.get("text") or []
            elif isinstance(ans, (list, tuple)):
                texts = list(ans)
            else:
                try:
                    texts = list(ans) if ans is not None else []
                except Exception:
                    texts = []
            a = str(texts[0]) if len(texts) else ""
            q = str(row.get("question", "") or "")
            ctx = str(row.get("context", "") or "")
            imp = str(row.get("is_impossible", "")).lower() in ("true", "1")
            if q and a and not imp:
                out.append(_case(len(out), q, a, ctx, "dureader"))
    return out


def from_drcd(limit: int) -> list[dict]:
    """DRCD 繁体中文阅读理解；结构同 SQuAD（context/question/answers）。"""
    from datasets import load_dataset
    # 社区镜像名可能变动；优先试通用名，失败给出提示
    ds = None
    for name in ("drcd", "Suchae/DRCD", "zh-plus/drcd"):
        try:
            ds = load_dataset(name, split="train")
            break
        except Exception:
            continue
    if ds is None:
        raise RuntimeError("DRCD 自动加载失败：请在 HF 搜索 DRCD 找到可用镜像名，或改用 cmrc2018。")
    out = []
    for i, ex in enumerate(ds):
        if limit and len(out) >= limit:
            break
        ans_list = (ex.get("answers") or {}).get("text") or []
        ans = ans_list[0] if ans_list else ""
        q = ex.get("question", "")
        ctx = ex.get("context", "")
        if q and ans:
            out.append(_case(i, q, ans, ctx, "drcd"))
    return out


def _auto_download_multidoc(data_dir: str) -> str:
    """自动下载 yuyijiong/Multi-Doc-QA-Chinese 到 data_dir（用 huggingface_hub，省去手动 CLI）。
    已存在 json 就跳过下载。gated 集若需登录会抛错，提示用户先 huggingface-cli login。"""
    from pathlib import Path as _P
    d = _P(data_dir)
    existing = list(d.glob("*.json")) + list(d.glob("*.jsonl"))
    if existing:
        return data_dir
    d.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id="yuyijiong/Multi-Doc-QA-Chinese", repo_type="dataset",
                          local_dir=data_dir, allow_patterns=["*.json", "*.jsonl"])
    except Exception as e:
        raise RuntimeError(
            f"自动下载 Multi-Doc-QA-Chinese 失败：{e}\n"
            f"该集需在 HF 同意条款。请先在网页 huggingface.co/datasets/yuyijiong/Multi-Doc-QA-Chinese "
            f"点同意，再执行 huggingface-cli login 填入你的 HF token（设 HF_TOKEN 环境变量亦可），然后重试。"
        )
    return data_dir


def from_multidoc(limit: int, data_dir: str) -> list[dict]:
    """yuyijiong/Multi-Doc-QA-Chinese：自动下载到本地后解析（每条含参考文档与问答）。
    只取「问题 + 基于参考文档的答案 + 参考文档段落」。结构较自由（chatml / raw），宽松解析，
    解析不出的样本跳过。最贴近 RAG「多文档里抽取」场景。许可 cc-by-nc-4.0（非商用）。"""
    _auto_download_multidoc(data_dir)
    out = []
    files = list(Path(data_dir).glob("**/*.json")) + list(Path(data_dir).glob("**/*.jsonl"))
    if not files:
        raise RuntimeError(f"{data_dir} 下没找到 json（下载可能失败，见上方提示）。")
    idx = 0
    for fp in files:
        if limit and len(out) >= limit:
            break
        try:
            text = fp.read_text(encoding="utf-8")
            rows = [json.loads(l) for l in text.splitlines() if l.strip()] if fp.suffix == ".jsonl" else json.loads(text)
            if isinstance(rows, dict):
                rows = rows.get("data", rows.get("rows", []))
        except Exception:
            continue
        for r in rows:
            if limit and len(out) >= limit:
                break
            if not isinstance(r, dict):
                continue
            q = r.get("question") or r.get("query") or r.get("instruction") or ""
            a = r.get("answer") or r.get("response") or r.get("output") or ""
            ctx = r.get("reference") or r.get("context") or r.get("positive_doc") or r.get("input") or ""
            if isinstance(ctx, list):
                ctx = " ".join(str(x) for x in ctx[:1])
            # chatml 形态：从 messages/conversations 里抽 user 问、assistant 答
            if (not q or not a) and isinstance(r.get("messages") or r.get("conversations"), list):
                msgs = r.get("messages") or r.get("conversations")
                for m in msgs:
                    role = (m.get("role") or m.get("from") or "").lower()
                    content = m.get("content") or m.get("value") or ""
                    if role in ("user", "human") and not q:
                        q = content
                    elif role in ("assistant", "gpt") and not a:
                        a = content
            if q and a:
                out.append(_case(idx, str(q), str(a), str(ctx), "multidoc"))
                idx += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="下载公开中文 QA 数据集 → golden_cases 格式")
    ap.add_argument("--dataset", required=True, choices=["cmrc2018", "dureader", "drcd", "multidoc"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=5000, help="最多取多少条（0=全部）")
    ap.add_argument("--subset", default="robust", help="dureader 子集：robust 或 checklist")
    ap.add_argument("--multidoc_dir", default="/root/autodl-tmp/data/multidoc_raw",
                    help="multidoc 专用：已下载的本地目录")
    args = ap.parse_args()

    if args.dataset == "cmrc2018":
        cases = from_cmrc2018(args.limit)
    elif args.dataset == "dureader":
        cases = from_dureader(args.limit, args.subset)
    elif args.dataset == "drcd":
        cases = from_drcd(args.limit)
    else:
        cases = from_multidoc(args.limit, args.multidoc_dir)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    print(f"[fetch_public_zh_data] {args.dataset}：转出 {len(cases)} 条 → {out}")
    print(f"下一步：python -m hashmm.training.build_sft_data --golden {out} "
          f"--out_dir /root/autodl-tmp/data/sft_public --style retrieval")


if __name__ == "__main__":
    main()

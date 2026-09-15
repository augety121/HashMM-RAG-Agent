#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/lora_to_gguf.py — 把自训 LoRA 合并进基座并导出 GGUF Q4_K_M，供桌面端本地推理（方案3）。

产物就是桌面 app 加载的本地模型文件（默认名 qwen2.5-7b-hashmm-q4_k_m.gguf）。导出后拷到桌面端
模型目录即可在「本地模型」开关下全程离线作答，文档与问答数据一字不出设备。

⚠️ 真机脚本（需 GPU + torch/transformers/peft + 已编译的 llama.cpp）。沙箱不可跑，仅供真机执行；
本仓沙箱只做 py_compile 语法校验。命令已按当前 llama.cpp 核实：
  - 转换：python convert_hf_to_gguf.py <hf_dir> --outtype f16 --outfile <f16.gguf>
  - 量化：./llama.cpp/build/bin/llama-quantize <f16.gguf> <out.gguf> Q4_K_M
（旧脚本名 convert-hf-to-gguf.py / quantize 已分别更名为下划线版与 llama-quantize。）

典型用法（在 AutoDL / 本地 GPU 机）：
  # 1) 已 build 好 llama.cpp（带 GGML_CUDA 可选）：
  #    git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp && cmake -B build && cmake --build build -j
  # 2) 合并 + 导出：
  python scripts/lora_to_gguf.py \
      --base models/Qwen2.5-7B-Instruct \
      --lora models/qwen2.5-7b-hashmm-final-v2 \
      --llama-cpp /root/llama.cpp \
      --out dist/qwen2.5-7b-hashmm-q4_k_m.gguf
  # 3) 把产物拷到桌面端模型目录（用户数据区 models/，文件名需与 DEFAULT_LLM_FILE 一致）：
  #    Windows: %APPDATA%/HashMM/models/qwen2.5-7b-hashmm-q4_k_m.gguf
  #    （桌面 app「本地模型」开关检测到该文件即可启用）

无 --lora 时直接转换基座（不合并），方便先验证全链路。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], cwd: str | None = None) -> None:
    print("· 执行:", " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if r.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {r.returncode}）: {' '.join(map(str, cmd))}")


def _find_convert_script(llama_cpp_dir: Path) -> Path:
    """定位 convert_hf_to_gguf.py（兼容历史命名 convert-hf-to-gguf.py）。"""
    for name in ("convert_hf_to_gguf.py", "convert-hf-to-gguf.py"):
        p = llama_cpp_dir / name
        if p.exists():
            return p
    raise SystemExit(f"未在 {llama_cpp_dir} 找到 convert_hf_to_gguf.py，请确认 --llama-cpp 指向 llama.cpp 仓库根。")


def _find_quantize_bin(llama_cpp_dir: Path) -> Path:
    """定位 llama-quantize 可执行（兼容旧名 quantize；兼容多种 build 输出目录）。"""
    candidates = [
        llama_cpp_dir / "build" / "bin" / "llama-quantize",
        llama_cpp_dir / "build" / "bin" / "Release" / "llama-quantize.exe",
        llama_cpp_dir / "llama-quantize",
        llama_cpp_dir / "build" / "bin" / "quantize",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise SystemExit(
        f"未找到 llama-quantize 可执行。请先编译 llama.cpp（cmake -B build && cmake --build build -j），"
        f"或检查 --llama-cpp 路径。已查找: {[str(c) for c in candidates]}")


def merge_lora(base: Path, lora: Path, out_dir: Path) -> None:
    """用 PEFT 把 LoRA 合并进基座并保存为完整 HF 模型。"""
    print(f"· 合并 LoRA: base={base} + lora={lora} -> {out_dir}")
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except Exception as e:
        raise SystemExit(f"缺少依赖（需 torch/transformers/peft）：{e}\n  pip install torch transformers peft")

    import torch as _torch
    tok = AutoTokenizer.from_pretrained(str(base), trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(base), torch_dtype=_torch.float16, trust_remote_code=True, device_map="cpu")
    model = PeftModel.from_pretrained(model, str(lora))
    model = model.merge_and_unload()       # 把 LoRA 增量写回基座权重
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir), safe_serialization=True)
    tok.save_pretrained(str(out_dir))
    print(f"· 合并完成 -> {out_dir}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LoRA 合并 + 导出 GGUF Q4_K_M（桌面本地推理）")
    ap.add_argument("--base", default="models/Qwen2.5-7B-Instruct", help="基座模型 HF 目录")
    ap.add_argument("--lora", default="", help="LoRA 适配器目录（留空=不合并，直接转换基座）")
    ap.add_argument("--llama-cpp", required=True, dest="llama_cpp", help="已编译的 llama.cpp 仓库根路径")
    ap.add_argument("--out", default="dist/qwen2.5-7b-hashmm-q4_k_m.gguf", help="输出 GGUF 路径")
    ap.add_argument("--quant", default="Q4_K_M", help="量化级别（默认 Q4_K_M）")
    ap.add_argument("--keep-f16", action="store_true", help="保留中间 f16 GGUF（默认删除）")
    args = ap.parse_args(argv)

    base = Path(args.base).resolve()
    llama_cpp = Path(args.llama_cpp).resolve()
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not base.exists():
        raise SystemExit(f"基座模型目录不存在：{base}")
    convert_py = _find_convert_script(llama_cpp)
    quant_bin = _find_quantize_bin(llama_cpp)

    work = Path(tempfile.mkdtemp(prefix="hashmm_gguf_"))
    try:
        # 1) 需要合并就先合并，否则直接拿基座目录转
        hf_dir = base
        if args.lora:
            lora = Path(args.lora).resolve()
            if not lora.exists():
                raise SystemExit(f"LoRA 目录不存在：{lora}")
            hf_dir = work / "merged"
            merge_lora(base, lora, hf_dir)

        # 2) HF -> f16 GGUF
        f16 = work / "model-f16.gguf"
        _run([sys.executable, convert_py, str(hf_dir), "--outtype", "f16", "--outfile", str(f16)])
        if not f16.exists():
            raise SystemExit("转换未产出 f16 GGUF，请检查 convert 日志。")

        # 3) f16 -> Q4_K_M
        _run([quant_bin, str(f16), str(out), args.quant])
        if not out.exists():
            raise SystemExit("量化未产出目标 GGUF，请检查 llama-quantize 日志。")

        size_gb = out.stat().st_size / (1024 ** 3)
        print("\n✅ 完成：", out, f"（约 {size_gb:.1f} GB）")
        print("下一步：把该文件拷到桌面端模型目录，文件名保持 qwen2.5-7b-hashmm-q4_k_m.gguf：")
        print("  Windows: %APPDATA%/HashMM/models/")
        print("  macOS:   ~/Library/Application Support/HashMM/models/")
        print("  Linux:   ~/.config/HashMM/models/")
        print("桌面 app「本地模型」开关检测到该文件 + 已装 node-llama-cpp 即可全程离线作答。")
        return 0
    finally:
        if not args.keep_f16:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("· 中间产物保留在:", work)


if __name__ == "__main__":
    raise SystemExit(main())

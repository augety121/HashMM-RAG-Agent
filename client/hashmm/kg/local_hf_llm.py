"""In-process local LLM via Hugging Face transformers — for KG extraction.

Why this exists (this machine's constraints)
--------------------------------------------
- Latest vLLM cannot run here: it forces a CUDA-13 torch, but the host driver is
  CUDA 12.4 → ``torch.cuda.is_available()`` becomes False. (Two attempts proved this.)
- ollama.com and huggingface.co are not reachable from this container.
- What *does* work: the restored ``torch 2.5.1+cu124`` (GPU ok), the already-installed
  ``transformers``, and **ModelScope** for weights.

So we load a chat model **in-process** with transformers and expose a
``fn(prompt) -> str`` that is drop-in for the KG extractor's ``llm_fn``.

Cost: zero (local GPU). Speed: sequential ``generate`` (no server batching) — fine
for a one-time overnight rebuild, especially with the chunk pre-filter on. Use a
smaller model (e.g. Qwen2.5-3B-Instruct) if you want it faster.

All heavy imports (torch/transformers) are lazy, inside :func:`build_hf_llm_fn`,
so importing this module never affects any other code path.
"""
from __future__ import annotations

import threading
from typing import Callable

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.local_hf_llm")


def build_hf_llm_fn(
    model_path: str,
    max_new_tokens: int = 1024,
    temperature: float = 0.1,
    device: str | None = None,
) -> Callable[[str], str]:
    """Load a local HF chat model once and return ``fn(prompt) -> str``.

    Args:
        model_path: local directory (downloaded via ModelScope) or a model id.
        max_new_tokens: output cap (extraction needs little; keep small = faster).
        temperature: 0.1 for near-deterministic extraction.
        device: 'cuda' / 'cpu'; auto-detected when None.

    The returned callable is thread-safe (a lock serialises GPU access, since
    one model on one CUDA stream cannot truly run concurrent generates), so it
    works even if called from the extractor's thread pool — just set
    ``--workers 1`` because parallelism gives no speedup here.
    """
    import os  # cheap; needed for the path check below

    # If model_path looks like a local path it MUST exist as a directory with
    # model files. Otherwise transformers treats it as a Hub repo id and raises a
    # confusing "Repo id must be in the form ..." error (and this box can't reach
    # the Hub anyway). Check FIRST — before importing torch — so the failure is a
    # clear, actionable message rather than a heavy import + cryptic error.
    looks_local = model_path.startswith(("/", "./", "../", "~")) or (os.sep in model_path)
    expanded = os.path.expanduser(model_path)
    if looks_local and not os.path.isdir(expanded):
        raise FileNotFoundError(
            f"本地模型目录不存在: {model_path} 。请确认已把 Qwen2.5 下载到该目录"
            f"（ModelScope，huggingface 不通时用：modelscope download "
            f"--model Qwen/Qwen2.5-3B-Instruct --local_dir {model_path}），"
            f"或先用 `ls` 看真实目录名后修改 HASHMM_KG_LLM_MODEL。"
        )
    if looks_local:
        model_path = expanded
    local_only = looks_local  # this box can't reach the Hub; force offline load

    import torch  # lazy
    from transformers import AutoModelForCausalLM, AutoTokenizer  # lazy

    max_new_tokens_default = max_new_tokens  # per-call override falls back to this
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Loading local HF model from {model_path} on {dev} ...")
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, trust_remote_code=True, local_files_only=local_only
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=(torch.float16 if dev == "cuda" else torch.float32),
        device_map=dev,
        trust_remote_code=True,
        local_files_only=local_only,
    )
    model.eval()
    lock = threading.Lock()
    logger.info("Local HF model ready.")

    def fn(prompt, max_new_tokens: int | None = None) -> str:
        # Robust to BOTH call styles: a plain string prompt, OR a messages list
        # like [{"role","content"}, ...]. The KG extractor / community summarizer
        # may call with either, so normalise here instead of guessing upstream.
        if isinstance(prompt, list):
            messages = [m for m in prompt
                        if isinstance(m, dict) and "role" in m and "content" in m]
            if not messages:
                messages = [{"role": "user", "content": str(prompt)}]
        else:
            messages = [{"role": "user", "content": str(prompt)}]
        try:
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            # Tokenizers without a chat template: concatenate message contents.
            text = "\n\n".join(str(m.get("content", "")) for m in messages)
        if not isinstance(text, str):
            text = str(text)
        gen_kwargs = dict(
            max_new_tokens=int(max_new_tokens or max_new_tokens_default),
            pad_token_id=(tokenizer.eos_token_id or tokenizer.pad_token_id),
        )
        if temperature and temperature > 0:
            gen_kwargs.update(do_sample=True, temperature=float(temperature))
        else:
            gen_kwargs.update(do_sample=False)
        # v17: serialize the ENTIRE call (tokenize + generate + decode), not just
        # generate(). A single HF model on one CUDA stream is not safe to drive
        # from multiple threads — concurrent tokenization/decoding corrupts inputs
        # and the model returns empty []. (This caused ~99% empty under workers=8
        # while sequential extraction yielded ~60%.)
        with lock:
            inputs = tokenizer(text, return_tensors="pt").to(dev)
            with torch.no_grad():
                out = model.generate(**inputs, **gen_kwargs)
            gen_ids = out[0][inputs["input_ids"].shape[1]:]
            return tokenizer.decode(gen_ids, skip_special_tokens=True)

    fn.model_name = str(model_path)  # type: ignore[attr-defined]
    fn.is_local_hf = True  # type: ignore[attr-defined]  # marks GPU-serial model
    return fn

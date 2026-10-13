"""hashmm/training/infer_lora.py — 训练后快速验证：挂上 LoRA 看模型是否学会了 Search-R1 格式。

4bit 加载本地基座 + 挂训练好的 LoRA 适配器，对几条问题生成，检查输出里是否出现
<think>/<search>/<answer> 标签。单卡 4090 推理只占十几 G，很轻。

用法：
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.infer_lora \
        --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \
        --lora  /root/autodl-tmp/models/qwen2.5-7b-hashmm-lora \
        --q "网易2024年游戏业务收入是多少？"
"""
from __future__ import annotations

import argparse

_USER_TEMPLATE = (
    "Answer the given question. You must conduct reasoning inside <think> and </think> "
    "first every time you get new information. After reasoning, if you find you lack some "
    "knowledge, you can call a search engine by <search> query </search>, and it will return "
    "the top searched results between <information> and </information>. You can search as many "
    "times as you want. If you find no further external knowledge needed, you can directly "
    "provide the answer inside <answer> and </answer> without detailed illustrations. For "
    "example, <answer> Beijing </answer>. Question: {question}\n"
)


def main():
    ap = argparse.ArgumentParser(description="训练后推理验证（基座 + LoRA）")
    ap.add_argument("--model", default="/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
    ap.add_argument("--lora", default="/root/autodl-tmp/models/qwen2.5-7b-hashmm-lora")
    ap.add_argument("--q", default="网易2024年游戏业务收入是多少？", help="测试问题")
    ap.add_argument("--max_new", type=int, default=256)
    args = ap.parse_args()

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import PeftModel

    # 预检 GPU：4bit 推理必须有 CUDA。容器偶发「训练后显卡掉线/NVML 初始化失败」，
    # 这时给出可操作的提示，而不是抛一长串 traceback。
    if not torch.cuda.is_available():
        print("=" * 60)
        print("检测不到可用的 GPU（CUDA 不可用）。")
        print("这通常是容器里显卡临时掉线（常见于训练刚结束时）。处理：")
        print("  1) 先跑 nvidia-smi，看 4090 在不在；")
        print("  2) 若 nvidia-smi 也报错 → 在 AutoDL 控制台重启容器（关机再开机）；")
        print("  3) 重启后无需重新训练，LoRA 已在硬盘，直接重跑本命令即可。")
        print("=" * 60)
        return

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    base = AutoModelForCausalLM.from_pretrained(args.model, quantization_config=bnb,
                                                device_map={"": 0}, trust_remote_code=True)
    model = PeftModel.from_pretrained(base, args.lora)
    model.eval()

    msgs = [{"role": "user", "content": _USER_TEMPLATE.format(question=args.q)}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = tok(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=args.max_new, do_sample=False,
                             pad_token_id=tok.eos_token_id)
    text = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    print("=" * 60)
    print("问题：", args.q)
    print("模型输出：")
    print(text)
    print("=" * 60)
    has = {t: (t in text) for t in ("<think>", "<search>", "<answer>")}
    print("格式检查（学会 Search-R1 标签了吗）：", has)


if __name__ == "__main__":
    main()

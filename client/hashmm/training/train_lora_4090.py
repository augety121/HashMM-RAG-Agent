"""hashmm/training/train_lora_4090.py — 单张 RTX 4090（24G）上 LoRA+4bit 微调 Qwen2.5-7B-Instruct。

这是为「只有一张 4090」量身写的可落地训练脚本。原理：
  · 4bit 量化加载 7B（QLoRA）：7B 权重压到约 5–6G 显存；
  · 只训练 LoRA 适配器（很小一部分参数），base 权重冻结 → 24G 完全够；
  · 用 TRL 的 SFTTrainer 在「问题→Search-R1 格式轨迹」数据上做监督微调。
训练产物是一个 LoRA 适配器（几十 MB），推理时挂到 base 模型上即可。

依赖（你环境里已有 peft/transformers/datasets/accelerate/torch，只差这两个）：
    pip install bitsandbytes trl

运行（AutoDL 4090，模型已在本地）：
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.train_lora_4090 \
        --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \
        --data_dir /root/autodl-tmp/data/sft \
        --out_dir /root/autodl-tmp/models/qwen2.5-7b-hashmm-lora \
        --epochs 3

显存不够时（4090 偶发 OOM）依次尝试：--max_len 1024、--batch 1、--grad_accum 16。
"""
from __future__ import annotations

import argparse
import os


def main():
    ap = argparse.ArgumentParser(description="4090 单卡 LoRA+4bit 微调 Qwen2.5-7B-Instruct")
    ap.add_argument("--model", default="/root/autodl-tmp/models/Qwen2.5-7B-Instruct", help="本地基座模型目录")
    ap.add_argument("--data_dir", default="/root/autodl-tmp/data/sft", help="含 train.jsonl / val.jsonl")
    ap.add_argument("--out_dir", default="/root/autodl-tmp/models/qwen2.5-7b-hashmm-lora", help="LoRA 输出目录")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--batch", type=int, default=1, help="每步样本数（4090 建议 1）")
    ap.add_argument("--grad_accum", type=int, default=8, help="梯度累积（等效 batch = batch*grad_accum）")
    ap.add_argument("--lr", type=float, default=2e-4, help="LoRA 学习率")
    ap.add_argument("--max_len", type=int, default=2048, help="最大序列长度，OOM 就调小")
    ap.add_argument("--lora_r", type=int, default=16)
    ap.add_argument("--lora_alpha", type=int, default=32)
    ap.add_argument("--no_eval", action="store_true", help="关闭中途评估（省显存；OOM 时可加）")
    args = ap.parse_args()

    # 延迟导入：只有真正训练时才需要这些重库（也让 import hashmm.training 时不报错）
    import torch
    from datasets import load_dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer, SFTConfig

    train_file = os.path.join(args.data_dir, "train.jsonl")
    val_file = os.path.join(args.data_dir, "val.jsonl")
    if not os.path.exists(train_file):
        raise FileNotFoundError(f"训练数据不存在：{train_file}（先跑 build_sft_data.py）")

    print(f"[train] 加载分词器与 4bit 基座：{args.model}")
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # 4bit 量化配置（QLoRA 标准设置：nf4 + double quant + bf16 计算）
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=bnb, device_map={"": 0},
        trust_remote_code=True, torch_dtype=torch.bfloat16,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    # LoRA：只训练注意力与 MLP 的投影矩阵（Qwen2.5 标准目标模块）
    lora = LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    data_files = {"train": train_file}
    if os.path.exists(val_file):
        data_files["validation"] = val_file
    ds = load_dataset("json", data_files=data_files)

    cfg = SFTConfig(
        output_dir=args.out_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_length=args.max_len,
        logging_steps=5,
        save_strategy="epoch",
        eval_strategy=("epoch" if ("validation" in data_files and not args.no_eval) else "no"),
        # —— 评估阶段省显存（你那次 OOM 正是崩在评估，不是训练）——
        per_device_eval_batch_size=1,          # 评估也一条条来
        eval_accumulation_steps=1,             # 每步就把中间结果挪到 CPU，不在显存里攒
        prediction_loss_only=True,             # 评估只算 loss，不物化 [N×词表] 巨型 logits（最关键）
        bf16=True,
        gradient_checkpointing=True,          # 省显存关键开关
        optim="paged_adamw_8bit",             # 8bit 优化器，再省显存
        report_to="none",
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
    )

    trainer = SFTTrainer(
        model=model,
        args=cfg,
        train_dataset=ds["train"],
        eval_dataset=ds.get("validation"),
        processing_class=tokenizer,
    )
    print(f"[train] 开始训练：train={len(ds['train'])} 条，等效 batch={args.batch * args.grad_accum}")
    try:
        trainer.train()
    except Exception as e:
        # OOM 等异常：尽量保存已训练的 LoRA（不浪费已花的时间），并给出可操作建议
        is_oom = "out of memory" in str(e).lower() or "OutOfMemory" in type(e).__name__
        if is_oom:
            print("\n[train] ⚠️ 显存不足（OOM）。已训练的进度会尝试保存，可用下面任一办法重训：")
            print("  1) 缩短序列长度（最有效）：加 --max_len 1024")
            print("  2) 关闭中途评估（评估占额外显存）：加 --no_eval")
            print("  3) 加大梯度累积、降低瞬时占用：--grad_accum 16")
            print("  例：python -m hashmm.training.train_lora_4090 --model %s --data_dir %s --out_dir %s --epochs %d --max_len 1024 --no_eval"
                  % (args.model, args.data_dir, args.out_dir, args.epochs))
            try:
                trainer.save_model(args.out_dir)
                tokenizer.save_pretrained(args.out_dir)
                print(f"[train] 已把中断前的 LoRA 存到 {args.out_dir}（可先用它验证，或按上面重训更稳）")
            except Exception:
                pass
        raise
    trainer.save_model(args.out_dir)
    tokenizer.save_pretrained(args.out_dir)
    print(f"[train] 完成。LoRA 适配器已存到 {args.out_dir}")
    print("推理验证：python -m hashmm.training.infer_lora --model", args.model, "--lora", args.out_dir)


if __name__ == "__main__":
    main()

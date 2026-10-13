"""Cross-modal deep hashing core.

Public API:
    encoders.TextEncoder, encoders.ImageEncoder — frozen pretrained backbones
    hash_net.CrossModalHashNet                  — two MLP heads, tanh→sign
    losses.HashLoss                              — pairwise sim + quant + balance
    dataset.CrossModalPairsDataset               — torch Dataset over pairs.jsonl
    train.train_hash_net                         — one-call training loop
    index.HashIndex                              — Faiss binary index wrapper
"""

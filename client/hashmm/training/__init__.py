"""hashmm.training — P2：用 Search-R1 配方训练「你专属的检索策略模型」的数据与训练脚手架。

不在生产路径里 import，纯离线脚本（在你的 GPU 服务器上跑）。两步：
  1. build_searchr1_data.py —— 把企业金标准集 / 公开多跳 QA 转成 Search-R1 训练格式（parquet）。
  2. load_and_train.py     —— 读取 + 校验数据，给出在 Qwen2.5-7B-Instruct 上训练的完整命令与配置。
"""

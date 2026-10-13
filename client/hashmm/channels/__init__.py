"""hashmm/channels/ — 即时通讯渠道接入（V105）。

把外部 IM（飞书 App Bot / 微信 iLink）接到 HashMM 的 RAG-Agent，让员工在飞书/微信里
直接问知识库。借鉴 fanbox 的微信 iLink 工程实践（消息通道 ↔ 本机大脑 ↔ 按会话续上下文）
与飞书开放平台官方应用机器人做法。

铁律：默认关（HASHMM_FEISHU_ENABLE / HASHMM_WECHAT_ENABLE）、关闭即零变化、永不抛错、
零新依赖（飞书 AES 复用既有 cryptography，签名用 stdlib hashlib/hmac）。
"""

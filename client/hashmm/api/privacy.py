"""hashmm/api/privacy.py — 企业隐私加固：日志/审计中的 PII 脱敏。

目标：用户查询、报错信息等在写入日志/审计/可观测系统【之前】，把邮箱、手机号、
身份证、银行卡、长数字 ID、Bearer/JWT、API key、密码字段等敏感信息掩码掉，
避免个人信息（PII）外泄到日志体系。

设计：纯函数、可单测、**永不抛错**（脱敏过程异常就退回截断后的原文，绝不影响主流程）。
就高不就低——宁可多掩一点（如 13 位以上纯数字一律按号码处理），也不放过敏感串。
"""
from __future__ import annotations
import re

# 预编译规则（应用顺序：先长/具体，后短/泛化，避免相互吞并）
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
_KV_SECRET = re.compile(
    r"(?i)\b(bearer|authorization|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|secret|password|passwd|pwd)\b\s*[:=]\s*[^\s,;]+"
)
_APIKEY = re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9]{12,}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CN_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")       # 身份证 18 位
_CN_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")                        # 中国大陆手机号
_LONG_NUM = re.compile(r"(?<!\d)\d{13,19}(?!\d)")                          # 银行卡等 13-19 位长号


def redact_pii(text) -> str:
    """把字符串中的常见 PII / 凭据掩码。永不抛错。"""
    if not text:
        return "" if text is None else str(text)
    try:
        s = str(text)
        s = _JWT.sub("<jwt>", s)
        s = _KV_SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", s)
        s = _APIKEY.sub("<key>", s)
        s = _EMAIL.sub("<email>", s)
        s = _CN_ID.sub("<id>", s)
        s = _CN_PHONE.sub("<phone>", s)
        s = _LONG_NUM.sub("<num>", s)
        return s
    except Exception:
        try:
            return str(text)[:200]
        except Exception:
            return "<unprintable>"


def redact_for_log(text, limit: int = 200) -> str:
    """脱敏 + 截断，专供日志使用（默认上限 200 字）。"""
    s = redact_pii(text)
    if limit and len(s) > limit:
        return s[:limit] + "…"
    return s

"""安全相关模块的聚合入口（P2-1 模块归类）。

为什么这样做（零破坏归类）：
项目顶层有 40 个散落的 .py，内聚性弱、新人难找。彻底的做法是物理移动文件 + 子包，
但那会牵动大量 `from hashmm.xxx import` 引用（含上千个测试），风险极高。

折中且零破坏的方案：**保持原模块位置不动**（所有旧 import 照常工作），
额外提供一个按职责分组的**聚合入口**。新代码可以用整洁的：
    from hashmm.security import detect_prompt_injection, scan_chunk_for_injection, evaluate
而不必记住每个符号在哪个顶层模块。旧的 `from hashmm.prompt_safety import ...` 完全不受影响。

归入本入口的安全相关模块：
  - access_control     文档级 ACL / 结果过滤
  - agent_safety       工具风险分级
  - prompt_safety      prompt 注入检测
  - rag_security       检索内容注入扫描 / 清洗
  - secrets_crypto     密钥加解密
  - security_policy    统一安全策略中心（P1-1，三层收口）
"""
from __future__ import annotations

# 逐模块 re-export 关键公开符号（按需扩展）。用 try/except 保证单个模块缺失不影响其余。
try:
    from hashmm.access_control import DocumentACL, filter_results, filter_sources  # noqa: F401
except Exception:
    pass

try:
    from hashmm.agent_safety import classify_tool_risk  # noqa: F401
except Exception:
    pass

try:
    from hashmm.prompt_safety import detect_prompt_injection  # noqa: F401
except Exception:
    pass

try:
    from hashmm.rag_security import scan_chunk_for_injection, sanitize_chunk_text  # noqa: F401
except Exception:
    pass

try:
    from hashmm.security_policy import (  # noqa: F401
        evaluate, risk_of, summary, Decision,
    )
except Exception:
    pass


def modules() -> list[str]:
    """返回归入安全分组的顶层模块名（便于发现/文档）。"""
    return [
        "access_control", "agent_safety", "prompt_safety",
        "rag_security", "secrets_crypto", "security_policy",
    ]

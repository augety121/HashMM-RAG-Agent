"""统一配置层（P0-2）—— 把散落的 129 个 HASHMM_ 开关集中登记、统一读取、自动生成文档。

为什么需要：此前 129 个开关散落在 203 处 os.environ 读取，无 schema、无默认值表、无文档 ——
新人（甚至作者）无法知道有哪些开关、默认什么、互相依赖什么。这是可维护性的硬伤。

设计原则（务实、零破坏）：
- **不强制迁移**：现有代码的 os.environ 读取保持不变（改 203 处风险高）。本层是**登记 + 统一入口 +
  文档**，新代码用 `settings.get(...)`，旧代码可渐进迁移。
- **零行为变化**：所有默认值严格等于现有代码里的默认，开启逻辑一致。
- **可发现**：`generate_config_md()` 一键生成完整开关清单文档。

用法：
    from hashmm import settings
    settings.get_bool("HASHMM_LLM_ROUTING")        # 带默认值的布尔读取
    settings.get("HASHMM_LOCAL_LLM_PATH")          # 字符串
    settings.describe("HASHMM_KG_LLM_EXTRACT")     # 查某开关的说明/默认
    print(settings.generate_config_md())           # 生成文档
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Switch:
    env: str
    default: str
    kind: str          # bool / str / int / float / secret / path
    category: str
    desc: str


# ── 开关登记表（按类别）。默认值严格对齐现有代码，开启即零变化基线 ──
_SWITCHES: list[Switch] = [
    # 核心 / 环境
    Switch("HASHMM_ENV", "production", "str", "core", "运行环境 (production/dev)"),
    Switch("HASHMM_DB_PATH", "", "path", "core", "SQLite 数据库路径（空=默认 data 下）"),
    Switch("HASHMM_DB_POOL_SIZE", "16", "int", "core", "DB 连接池大小"),
    Switch("HASHMM_CORS_ORIGINS", "", "str", "core", "允许的 CORS 源（逗号分隔）"),
    Switch("HASHMM_REQUIRE_AUTH", "0", "bool", "core", "是否强制所有接口鉴权"),
    Switch("HASHMM_ALLOW_INSECURE", "0", "bool", "core", "允许不安全配置启动（不建议生产）"),

    # 安全 / 鉴权
    Switch("HASHMM_JWT_SECRET", "", "secret", "security", "JWT 签名密钥（生产必设随机长串）"),
    Switch("HASHMM_API_KEY", "", "secret", "security", "对外 /v1 API key"),
    Switch("HASHMM_MCP_TOKEN", "", "secret", "security", "MCP server 鉴权 token"),
    Switch("HASHMM_METRICS_TOKEN", "", "secret", "security", "/metrics 鉴权 token"),
    Switch("HASHMM_METRICS_PUBLIC", "0", "bool", "security", "公开 /metrics（不鉴权，仅内网）"),
    Switch("HASHMM_TOOL_APPROVAL", "0", "bool", "security", "工具调用需审批"),
    Switch("HASHMM_AUDIT_TOOLS", "0", "bool", "security", "审计工具调用"),
    Switch("HASHMM_AUDIT_DIR", "", "path", "security", "审计日志目录"),

    # F11 LLM 路由
    Switch("HASHMM_LLM_ROUTING", "0", "bool", "llm", "F11 云-本地路由总开关"),
    Switch("HASHMM_LOCAL_LLM_PATH", "", "path", "llm", "本地 LLM 模型路径（F11 必需）"),
    Switch("HASHMM_LLM_TASK_ROUTING", "", "str", "llm", "任务→后端路由覆盖(JSON)"),
    Switch("HASHMM_PRIVACY_LOCAL", "0", "bool", "llm", "隐私模式：全部走本地，不外发云端"),

    # 检索
    Switch("HASHMM_RETRIEVAL_STRATEGY", "", "str", "retrieval", "强制检索策略"),
    Switch("HASHMM_KG_RETRIEVAL", "0", "bool", "retrieval", "启用 KG 增强检索"),
    Switch("HASHMM_AGENTIC_RETRIEVAL", "0", "bool", "retrieval", "启用 agentic 检索"),
    Switch("HASHMM_SEARCH_BACKEND", "", "str", "retrieval", "强制联网搜索后端"),
    Switch("HASHMM_WEB_FALLBACK", "0", "bool", "retrieval", "检索不到时联网兜底"),

    # KG（最多，33 个，登记高频核心）
    Switch("HASHMM_KG_LLM_EXTRACT", "0", "bool", "kg", "用 LLM 抽取 KG 实体关系"),
    Switch("HASHMM_KG_LLM_HF_PATH", "", "path", "kg", "KG 抽取用本地 HF 模型路径"),
    Switch("HASHMM_KG_LLM_MODEL", "", "str", "kg", "KG 抽取用模型名"),
    Switch("HASHMM_KG_LLM_MAX_CHUNKS", "0", "int", "kg", "KG 抽取最大 chunk 数"),
    Switch("HASHMM_KG_AUTO", "0", "bool", "kg", "自动建图"),
    Switch("HASHMM_KG_GLEANINGS", "0", "int", "kg", "KG 多轮 gleaning 次数"),
    Switch("HASHMM_KG_TEMPORAL", "0", "bool", "kg", "时序/双时态 KG"),
    Switch("HASHMM_KG_EVOLUTION", "0", "bool", "kg", "自进化 KG 待审区"),
    Switch("HASHMM_KG_LAZY_SUMMARIES", "0", "bool", "kg", "社区摘要懒加载"),
    Switch("HASHMM_KG_RESOLVE_DROP_NOISE", "0", "bool", "kg", "实体消解时丢弃噪声"),
    Switch("HASHMM_KG_FEWSHOT_RICH", "0", "bool", "kg", "KG 抽取用丰富 few-shot"),

    # Agent
    Switch("HASHMM_AGENTIC_RAG", "0", "bool", "agent", "启用 agentic RAG"),
    Switch("HASHMM_AGENTIC_MAX_HOPS", "3", "int", "agent", "agentic 最大跳数"),
    Switch("HASHMM_AGENTIC_LOCAL_LOOPS", "0", "int", "agent", "本地 agent 循环数"),
    Switch("HASHMM_AGENT_HITL", "0", "bool", "agent", "human-in-the-loop"),
    Switch("HASHMM_AGENT_TIMELINE", "0", "bool", "agent", "agent 运行时间线"),
    Switch("HASHMM_AGENT_STATE_DIR", "", "path", "agent", "agent 状态持久化目录"),
    # 第一阶段新增能力开关（S1-4 迁移登记，默认对齐现状零变化）
    Switch("HASHMM_PARALLEL_TOOLS", "0", "bool", "agent", "并行执行只读工具（对标 FuturesOrdered）"),
    Switch("HASHMM_PARALLEL_TOOLS_MAX", "4", "int", "agent", "并行工具并发上限"),
    Switch("HASHMM_PROMPT_CACHE", "0", "bool", "agent", "确定性辅助任务的 prompt 结果缓存"),
    Switch("HASHMM_PROMPT_CACHE_TTL", "3600", "int", "agent", "prompt 缓存 TTL（秒）"),
    Switch("HASHMM_SUBAGENTS", "0", "bool", "agent", "启用子代理"),
    Switch("HASHMM_CONFIDENCE", "0", "bool", "agent", "启用置信度评估"),

    # 平台能力
    Switch("HASHMM_MCP_SERVER", "0", "bool", "platform", "MCP server 暴露端"),
    Switch("HASHMM_PUBLIC_API", "0", "bool", "platform", "对外 /v1 RESTful API"),
    Switch("HASHMM_PUBLIC_API_OPEN", "0", "bool", "platform", "对外 API 免鉴权（仅内网）"),
    Switch("HASHMM_MULTI_TENANT", "0", "bool", "platform", "多租户隔离"),
    Switch("HASHMM_SCHEDULER", "0", "bool", "platform", "定时任务调度"),
    Switch("HASHMM_DESIGN_RENDER", "0", "bool", "platform", "受控设计渲染器"),

    # Eval
    Switch("HASHMM_EVAL_EXEC", "0", "bool", "eval", "eval 执行代码工具"),
    Switch("HASHMM_EVAL_HOLDOUT", "0", "bool", "eval", "eval held-out 保真"),

    # 搜索 API key
    Switch("HASHMM_SERPER_API_KEY", "", "secret", "search", "Serper.dev API Key"),
    Switch("HASHMM_BING_API_KEY", "", "secret", "search", "Bing Search API Key"),
    Switch("HASHMM_TAVILY_API_KEY", "", "secret", "search", "Tavily API Key"),

    # 缓存
    Switch("HASHMM_REDIS_HOST", "", "str", "cache", "Redis 主机（空=内存缓存）"),
]

_BY_ENV = {s.env: s for s in _SWITCHES}

_TRUE = {"1", "true", "yes", "on"}


def get(env: str, default: str | None = None) -> str:
    """读字符串配置：env 优先，否则用登记默认/传入默认。"""
    v = os.environ.get(env)
    if v is not None:
        return v
    if default is not None:
        return default
    sw = _BY_ENV.get(env)
    return sw.default if sw else ""


def get_bool(env: str, default: bool | None = None) -> bool:
    raw = os.environ.get(env)
    if raw is None:
        if default is not None:
            return default
        sw = _BY_ENV.get(env)
        raw = sw.default if sw else "0"
    return str(raw).strip().lower() in _TRUE


def get_int(env: str, default: int = 0) -> int:
    raw = os.environ.get(env)
    if raw is None:
        sw = _BY_ENV.get(env)
        raw = sw.default if sw else str(default)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def describe(env: str) -> dict | None:
    sw = _BY_ENV.get(env)
    if not sw:
        return None
    return {"env": sw.env, "default": sw.default, "kind": sw.kind,
            "category": sw.category, "desc": sw.desc}


def all_switches() -> list[Switch]:
    return list(_SWITCHES)


def generate_config_md() -> str:
    """生成完整开关清单文档（CONFIG.md）。"""
    cats: dict[str, list[Switch]] = {}
    for s in _SWITCHES:
        cats.setdefault(s.category, []).append(s)
    out = ["# HashMM 配置开关清单\n",
           "> 由 `hashmm/settings.py` 自动生成。所有开关默认值即'零行为变化'基线。\n",
           "> 注：本表登记核心高频开关；项目共有约 129 个 HASHMM_ 开关，其余为内部细调。\n"]
    cat_names = {"core": "核心/环境", "security": "安全/鉴权", "llm": "F11 LLM 路由",
                 "retrieval": "检索", "kg": "知识图谱", "agent": "Agent",
                 "platform": "平台能力", "eval": "评估", "search": "联网搜索", "cache": "缓存"}
    for cat in ["core", "security", "llm", "retrieval", "kg", "agent", "platform", "eval", "search", "cache"]:
        if cat not in cats:
            continue
        out.append(f"\n## {cat_names.get(cat, cat)}\n")
        out.append("| 开关 | 默认 | 类型 | 说明 |")
        out.append("|---|---|---|---|")
        for s in cats[cat]:
            dv = "(空)" if not s.default else s.default
            dv = "***" if s.kind == "secret" and s.default else dv
            out.append(f"| `{s.env}` | {dv} | {s.kind} | {s.desc} |")
    return "\n".join(out)


if __name__ == "__main__":
    print(generate_config_md())

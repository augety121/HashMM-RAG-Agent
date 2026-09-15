"""Session 运维（V204，对标 Qoder Cloud Agents）——运行时补丁 + 诊断助手。

- PATCH /api/conversations/{cid}/runtime   运行中改配置，下一轮生效，上下文不丢
- GET / DELETE 同路径                       查看 / 清空补丁
- POST  /api/conversations/{cid}/diagnose  会话跑挂/卡住/行为异常时一键诊断：
    自动收集 turns / tool_calls / errors / trace，启发式识别报错与根因，
    有 LLM 时再润色成结论；产出 markdown 报告，可直接复制贴工单。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api import session_runtime as srt
from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.routes.session_ops")

router = APIRouter(prefix="/api/conversations", tags=["session-ops"])


@router.get("/{conv_id}/runtime")
async def get_runtime(conv_id: str, request: Request):
    require_auth(request)
    return {"conv_id": conv_id, "overrides": srt.get_overrides(conv_id),
            "allowed_keys": list(srt._ALLOWED_KEYS), "effective": "next_turn"}


@router.patch("/{conv_id}/runtime")
async def patch_runtime(conv_id: str, request: Request):
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "补丁体为空：给出要修改的键（值传 null 表示删除该键）")
    ov = srt.patch_overrides(conv_id, body)
    try:
        db.audit(user["uid"], user["sub"], "session_patch", f"{conv_id}:{sorted(body.keys())}")
    except Exception as e:
        log_suppressed(logger, e)
    return {"ok": True, "conv_id": conv_id, "overrides": ov, "effective": "next_turn"}


@router.delete("/{conv_id}/runtime")
async def clear_runtime(conv_id: str, request: Request):
    require_auth(request)
    srt.clear_overrides(conv_id)
    return {"ok": True, "conv_id": conv_id, "overrides": {}}


# ── 诊断助手 ─────────────────────────────────────────────────────────────

_ERROR_PATTERNS = [
    ("timeout", "上游超时", "LLM/工具在时限内未返回", "缩小任务或提高超时；检查后端负载与网络；必要时切流畅档模型"),
    ("429", "限流", "上游返回 429 Too Many Requests", "降低并发/加重试退避；检查配额；错峰调用"),
    ("rate limit", "限流", "命中速率限制", "降低并发/加重试退避；检查配额"),
    ("api 密钥", "鉴权失败", "API Key 无效或过期", "在模型/后端设置里更新密钥后重试"),
    ("unauthorized", "鉴权失败", "上游 401/403", "更新密钥或检查该模型的访问权限"),
    ("llm 调用失败", "模型调用失败", "对话轮内 LLM 请求抛错", "看同名 trace 的错误明细；确认后端 LLM 服务在线"),
    ("llm 未就绪", "模型未就绪", "后端尚未加载/连接模型", "在「后端连接」里完成模型配置并等待就绪"),
    ("执行出错", "工具执行失败", "某个工具在执行期抛错", "看下方失败工具清单；核对参数与文件路径是否存在"),
    ("connection", "网络异常", "到上游/工具的连接失败", "检查网络与代理；本地服务确认端口在监听"),
]

_STOP_HINTS = {
    "max_iterations": ("循环达轮数上限", "任务过大或模型在打转（重复同类调用）", "把任务拆小；或用运行时补丁临时禁用打转的工具再续跑"),
    "budget_exceeded": ("上下文/预算耗尽", "累计上下文超过预算被止损", "开启/依赖长对话压缩；把大文件改成按需 read_file_range"),
    "deadline": ("到达时限", "单次运行超过 deadline", "拆分任务；后台长任务改 spawn_worker"),
    "no_progress": ("无进展短路", "连续多轮没有产出被止损", "换个说法明确目标；或补充缺失的输入文件"),
    "llm_error": ("模型报错终止", "LLM 连续失败", "见错误模式一节；先恢复模型服务再重试"),
}


def _heuristic_findings(messages: list[dict], traces: list[dict]) -> tuple[list[dict], dict]:
    findings: list[dict] = []
    stats = {"turns": len(messages), "tool_calls": 0, "tool_fail": 0, "errors": 0, "stop_reasons": []}
    text_pool: list[str] = []
    for m in messages:
        for k in ("content", "thinking"):
            v = m.get(k)
            if v:
                text_pool.append(str(v).lower())
        tc = m.get("tool_calls")
        if tc:
            s = str(tc)
            stats["tool_calls"] += s.count("name")
    fail_tools: dict[str, int] = {}
    slow_tools: list[str] = []
    for r in traces:
        kind = r.get("kind")
        if kind == "tool_call":
            stats["tool_calls"] = max(stats["tool_calls"], 0) + 0  # trace 口径单列
            if not r.get("ok", True):
                stats["tool_fail"] += 1
                fail_tools[r.get("name", "?")] = fail_tools.get(r.get("name", "?"), 0) + 1
            if int(r.get("latency_ms", 0) or 0) > 30000:
                slow_tools.append(f"{r.get('name')}({r.get('latency_ms')}ms)")
        elif kind == "error":
            stats["errors"] += 1
            text_pool.append(str(r.get("code", "")).lower())
        elif kind == "gen_ai_request":
            fr = r.get("gen_ai.response.finish_reasons") or r.get("finish_reason")
            if fr:
                stats["stop_reasons"].append(fr if isinstance(fr, str) else (fr[0] if fr else ""))
    blob = "\n".join(text_pool)
    seen = set()
    for pat, name, cause, fix in _ERROR_PATTERNS:
        if pat in blob and name not in seen:
            seen.add(name)
            findings.append({"type": "error_pattern", "name": name, "cause": cause, "fix": fix})
    for sr, (name, cause, fix) in _STOP_HINTS.items():
        if sr in blob:
            findings.append({"type": "stop_reason", "name": name, "cause": cause, "fix": fix})
    for tname, n in sorted(fail_tools.items(), key=lambda x: -x[1])[:5]:
        findings.append({"type": "tool_fail", "name": f"工具 {tname} 失败 {n} 次",
                         "cause": "参数/环境/上游其一不满足", "fix": f"单独重试 {tname} 并看返回错误原文"})
    if slow_tools:
        findings.append({"type": "slow", "name": "存在超慢工具调用", "cause": "；".join(slow_tools[:4]),
                         "fix": "网络类调低超时重试；本地重活考虑 spawn_worker 后台化"})
    if not findings:
        findings.append({"type": "none", "name": "未发现明显错误模式",
                         "cause": "近段消息与 trace 中没有已知故障特征",
                         "fix": "若表现异常，多为提示词/上下文问题：用运行时补丁 A/B 调 system_append 验证"})
    return findings, stats


def _render_report(conv_id: str, findings: list[dict], stats: dict, llm_note: str) -> str:
    lines = [f"# 会话诊断报告", f"- 会话：`{conv_id}`",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 采集：{stats['turns']} 条消息 · 工具失败 {stats['tool_fail']} 次 · 错误事件 {stats['errors']} 条",
             "", "## 结论与修复建议"]
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. **{f['name']}**")
        lines.append(f"   - 根因推断：{f['cause']}")
        lines.append(f"   - 修复建议：{f['fix']}")
    if llm_note:
        lines += ["", "## 模型补充分析", llm_note]
    lines += ["", "## 采集明细口径",
              "消息取该会话最近 60 条（含 thinking/tool_calls）；trace 取全局最近 300 条",
              "（tool_call / error / gen_ai_request 三类，JSONL 持久层 + 环形缓冲合并）。"]
    return "\n".join(lines)


@router.post("/{conv_id}/diagnose")
async def diagnose(conv_id: str, request: Request):
    user = require_auth(request)
    try:
        messages = db.get_messages(conv_id, limit=60) or []
    except Exception as e:
        log_suppressed(logger, e, "diagnose.messages")
        messages = []
    traces: list[dict] = []
    try:
        from hashmm import observability as ob
        traces = ob.trace_tail(600, kinds=("tool_call", "error", "gen_ai_request"))
        # V205 P0-2：trace 已带 conv_id → 精确按会话过滤；老日志没有该字段则回退全局尾巴
        _scoped = [r for r in traces if r.get("conv_id") == conv_id]
        if _scoped:
            traces = _scoped[-300:]
        else:
            traces = traces[-300:]
    except Exception as e:
        log_suppressed(logger, e, "diagnose.trace")
    findings, stats = _heuristic_findings(messages, traces)

    llm_note = ""
    try:
        from hashmm.api import app_state
        llm_fn = getattr(app_state, "llm_fn", None)
        if llm_fn and messages:
            tail = "\n".join(
                f"[{m.get('role')}] {str(m.get('content') or '')[:300]}" for m in messages[-8:]
            )[:3000]
            prompt = ("你是会话故障诊断员。基于以下会话尾部与启发式结论，用 3 条以内、"
                      "每条一句话补充最可能的根因或修复动作；没有新增洞见就回复 无。\n\n"
                      f"启发式结论：{[f['name'] for f in findings]}\n会话尾部：\n{tail}")
            import asyncio
            out = await asyncio.wait_for(asyncio.to_thread(llm_fn, prompt), timeout=25)
            note = str(out or "").strip()
            if note and note != "无":
                llm_note = note[:1200]
    except Exception as e:
        log_suppressed(logger, e, "diagnose.llm")

    report = _render_report(conv_id, findings, stats, llm_note)
    try:
        db.audit(user["uid"], user["sub"], "diagnose", conv_id)
    except Exception as e:
        log_suppressed(logger, e)
    return {"ok": True, "conv_id": conv_id, "findings": findings,
            "collected": stats, "report_md": report}

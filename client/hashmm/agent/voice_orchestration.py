"""语音任务编排（路线图阶段 D）——语音一句话，Agent 想周全、跨端闭环。

阶段 D 的收口：把前面 A/B/C 的能力串成端到端流水线。用户用**语音说一句**，
系统自动：转写 → 意图理解（复用阶段 B）→ 决策"本地直答还是派到桌面端执行"
（复用 P2-9 派活队列）→ 高质量交付（复用阶段 C 验收）。

关键设计——**语音场景比文字更需要 Agent 主动**：用户没在盯屏幕，所以：
- 意图模糊时，用一句**口语化**的追问（而非文字场景的书面澄清）；
- 需要看电脑文件/操作电脑的任务，自动判断应派到桌面端 runner（AutoDL 后端
  看不到用户桌面，必须派给客户端），而不是在服务器上白跑；
- 决策透明：返回结构化的编排结果，UI 可展示"我判断这是电脑任务，已派给你的桌面端"。

本模块只做**编排决策**（纯函数、可测），不直接执行——执行仍走 loop / dispatch。
失败安全：决策异常一律回退为"本地直答"。

主入口：
    orchestrate(text, history, user_prefs, has_desktop) -> Orchestration
      .action = "clarify" | "local" | "dispatch"
      .clarifying_question / .dispatch_kind / .dispatch_payload / .intent
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hashmm.agent import intent as _intent
from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.voice_orchestration")

# 需要"看/操作用户电脑"的信号 → 必须派到桌面端 runner（服务器做不到）
# 覆盖：本机文件/桌面、浏览器/网页操作、Computer Use 动作、办公文档
_DESKTOP_SIGNAL = re.compile(
    r"(我的?电脑|桌面|下载文件夹|我的?文件|本地文件|文件夹里|D盘|C盘|磁盘|我电脑上|"
    r"文件.*(发|传|放)到?对话|发.*文件|取.*文件|拿.*文件|"
    r"浏览器|网页|上网|打开(软件|应用|文件|网站)|"
    r"截图|截屏|点击|操作电脑|"
    r"word文档|excel表|word|excel|ppt)")

# 派活任务类型映射（与 App 工作台的 kind 对齐）
_KIND_BROWSER = re.compile(r"(浏览器|网页|上网|查(一下)?(新闻|资料|网站)|搜索网页|browser)")
_KIND_FILE = re.compile(r"(取(文件|文档)|把.*文件.*(发|传|放)|拿.*文件|读取本地|发我(的)?文件|文件.*发到|发到对话)")
_KIND_COMPUTER = re.compile(r"(盘点|列出.*文件|整理.*文件夹|清理.*文件|操作电脑|点击|截图)")


@dataclass
class Orchestration:
    action: str                              # clarify | local | dispatch
    intent: object = None                    # IntentResult
    clarifying_question: str = ""
    dispatch_kind: str = ""                  # browser_use | computer_use | file | seq
    dispatch_payload: dict = field(default_factory=dict)
    reason: str = ""                         # 决策理由（trace/UI 展示）

    def to_dict(self) -> dict:
        d = {"action": self.action, "reason": self.reason}
        if self.action == "clarify":
            d["clarifying_question"] = self.clarifying_question
        elif self.action == "dispatch":
            d["dispatch_kind"] = self.dispatch_kind
            d["dispatch_payload"] = self.dispatch_payload
        if self.intent is not None:
            d["intent"] = {"goal": getattr(self.intent, "goal", ""),
                           "confidence": getattr(self.intent, "confidence", 1.0),
                           "constraints": getattr(self.intent, "constraints", [])}
        return d


def _voice_question(base_question: str) -> str:
    """把书面澄清转成更口语的版本（语音场景用户在听，不在读）。"""
    # 去掉括号补充说明，句子更短更适合朗读
    q = re.sub(r"[（(].*?[)）]", "", base_question).strip()
    return q or "你想让我具体做什么？说一句就行。"


def _classify_dispatch_kind(text: str) -> str:
    if _KIND_BROWSER.search(text):
        return "browser_use"
    if _KIND_FILE.search(text):
        return "file"
    if _KIND_COMPUTER.search(text):
        return "computer_use"
    return "seq"  # 多步/不确定 → 交给桌面端序列执行，逐步确认


def orchestrate(text: str, history: list[dict] | None = None,
                user_prefs: dict | None = None,
                has_desktop: bool = False) -> Orchestration:
    """语音任务编排主入口。失败安全：异常回退为本地直答。"""
    try:
        t = (text or "").strip()
        if not t:
            return Orchestration(action="local", reason="空输入")

        # 1) 意图理解（复用阶段 B）——语音场景澄清用口语化问题
        ir = _intent.analyze(t, history=history, user_prefs=user_prefs)
        if ir.needs_clarification:
            return Orchestration(
                action="clarify", intent=ir,
                clarifying_question=_voice_question(ir.clarifying_question),
                reason=f"意图置信度 {ir.confidence}（{ir.ambiguity_reason}）")

        # 2) 是否需要用户电脑？→ 派到桌面端（服务器看不到桌面/无法 Computer/Browser Use）
        if _DESKTOP_SIGNAL.search(t):
            if not has_desktop:
                # 需要电脑但桌面端不在线 → 明确告知（而非默默在服务器白跑）
                return Orchestration(
                    action="clarify", intent=ir,
                    clarifying_question="这个任务需要用到你的电脑，但我没检测到在线的桌面端。请在电脑上登录同账号打开客户端，或用「接力」唤起后再说一次。",
                    reason="需要桌面端但当前离线")
            kind = _classify_dispatch_kind(t)
            return Orchestration(
                action="dispatch", intent=ir, dispatch_kind=kind,
                dispatch_payload={"task": t, "goal": ir.goal,
                                  "constraints": ir.constraints},
                reason=f"涉及本机文件/操作 → 派给桌面端执行（{kind}）")

        # 3) 纯知识/写作/推理 → 本地直答（带意图 hint 交给 Agent）
        return Orchestration(action="local", intent=ir,
                             reason="纯推理/写作任务，直接由 Agent 处理")
    except Exception as e:  # pragma: no cover
        logger.debug(f"voice orchestrate failed, fallback local: {e}")
        return Orchestration(action="local", reason="编排异常，回退本地")

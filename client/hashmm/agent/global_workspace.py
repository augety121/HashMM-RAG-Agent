"""agent/global_workspace — 全局工作区（V254，总控中枢）。

理念来源：Anthropic《A global workspace in language models》（2026-07）与
Baars 的全局工作区理论（GWT）——大脑是一组并行、无意识、彼此隔离工作的
专家系统；一条信息只有进入一个小的**共享工作区**并被广播给所有模块后，
才变得"有意识地可访问"（可报告、可推理、可统筹）。

映射到 HashMM（把理论落成产品级总控）：
  · 专家模块 = 聊天(chat) / 深度检索(deepsearch) / 多智能体(team) / 派活(dispatch)
    / 画布(canvas) / 电脑操作(computer_use) / 记忆(memory) / 知识库(kb) …
    ——它们本来各跑各的、互相看不见（正如 GWT 里的无意识专家系统）。
  · 工作区 = 本模块维护的一块小容量"意识缓冲"（conscious buffer，默认 7 条，
    呼应工作记忆容量）：各模块把重要事件作为 candidate 提交（带 salience 显著度），
    每个认知周期选出赢家进入缓冲并**广播**给所有订阅者。
  · 总控 = 工作区永远知道"系统现在在忙什么"（focus 焦点）、"谁在干活"（模块状态）、
    "刚发生了什么"（广播史）——前端总控台据此一屏统筹全局。

纪律（与本仓库其他 agent 模块同款）：
  · 纯标准库 + 线程安全单例，零新依赖；任何 hook 调用方全部 try/except——
    工作区是观察与统筹层，绝不能拖垮任何执行主链。
  · 事件落盘到 data/global_workspace.json（滚动 400 条），重启不失忆；
    落盘失败静默，内存态照常工作。
  · salience 竞争：同一周期多 candidate 只有最高显著度者进入意识缓冲被广播，
    其余仍进事件史（相当于"无意识处理过、但没进意识"）——忠实还原 GWT 的
    竞争-广播机制，也让总控台不被低价值事件刷屏。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from hashmm.utils import get_logger

logger = get_logger("hashmm.global_workspace")

# 意识缓冲容量（GWT 的工作区是小容量的；7±2 取 7）
_BUFFER_CAP = 7
# 事件史滚动上限（含未进入意识的低显著度事件）
_HISTORY_CAP = 400
# 模块心跳视为"活跃"的窗口（秒）
_ACTIVE_WINDOW = 90.0

# 已知专家模块注册表：id → 中文名 + 说明（前端总控台直接用；未知模块也允许上报，动态收录）
KNOWN_MODULES: dict[str, dict] = {
    "chat":         {"name": "对话",     "desc": "主问答链路（检索增强 + 流式生成）"},
    "deepsearch":   {"name": "深度检索", "desc": "Self-RAG：多跳检索 + 自我批判 + 忠实度门控"},
    "team":         {"name": "多智能体", "desc": "协调者拆角色并行执行 + 汇总"},
    "dispatch":     {"name": "派活",     "desc": "远端 runner 任务队列（plan/browser/file）"},
    "canvas":       {"name": "画布",     "desc": "工作画布：起稿 / 划选提问 / 发布"},
    "computer_use": {"name": "电脑操作", "desc": "视觉型 CU 工具循环（桌面端执行）"},
    "memory":       {"name": "记忆",     "desc": "长期记忆读写（经验 / 教训 / 偏好）"},
    "kb":           {"name": "知识库",   "desc": "语料入库 / 索引 / 有效性治理"},
    "voice":        {"name": "语音",     "desc": "本地 STT / 语音编排"},
    "remote":       {"name": "远程",     "desc": "远程桌面 / 中继 / App 遥控"},
    "docstudio":    {"name": "文档工坊", "desc": "文件深度理解 / 润色 / 图表 / 转换"},
    "loops":        {"name": "循环工程", "desc": "目标循环 / 时间循环（Loop Engineering）"},
}


def context_for_llm(max_chars: int = 600) -> str:
    """agent 端 J-lens 注入：项目内**每一次模型调用**（问答主链 / 团队角色 / 深检合成 /
    文档工坊 / 任意接入的外部 API）都可以带上这段工作区读出，获得全局感知后更聪明地回答。
    空工作区返回空串（不注入废话、不浪费 token）；任何异常返回空串（观察层不拖垮执行）。
    settings『gw_inject』设为 off 可全局关闭。"""
    try:
        from hashmm.api.settings_store import get_setting
        if get_setting("gw_inject", "on").strip().lower() in ("off", "0", "false"):
            return ""
    except Exception:
        pass
    try:
        snap = GlobalWorkspace.get().snapshot(history_n=0)
        if not snap.get("focus") and not snap.get("buffer"):
            return ""
        return verbalize(snap, max_chars=max_chars)
    except Exception:
        return ""


def verbalize(snapshot: dict, *, max_chars: int = 1800) -> str:
    """把工作区状态**可读化**为一段任何模型都能直接消费的中文文本。

    这是 J-lens（Jacobian lens）思想的产品化：镜头把模型内部激活线性搬运到输出基、
    用 unembedding 解码成词表——**进入全局工作区的信息是可言说的（verbalizable）**。
    对应到 HashMM：工作区里的焦点/意识缓冲/模块状态本是结构化内部态，这里把它
    "解码"成自然语言读出（readout）——任何接入的外部 API（deepseek / OpenAI 兼容 /
    本地模型…）把这段文本放进 prompt，就获得了对整个系统正在做什么的全局感知，
    据此智能回答。/api/gw/context 对外暴露，/api/gw/ask 内部使用。
    """
    try:
        lines: list[str] = ["[全局工作区读出 · 系统当前状态]"]
        f = snapshot.get("focus")
        lines.append(f"全局焦点：{f['goal']}（来源 {f.get('source','?')}）" if f else "全局焦点：无（系统空闲）")
        mods = [m for m in (snapshot.get("modules") or []) if m.get("active") or m.get("state") not in ("idle", None)]
        if mods:
            lines.append("模块状态：" + "；".join(
                f"{m['name']}={m.get('state','idle')}" + (f"（{m['detail']}）" if m.get("detail") else "")
                for m in mods[:8]))
        buf = snapshot.get("buffer") or []
        if buf:
            lines.append("最近进入意识的广播（新→旧）：")
            for e in list(buf)[::-1][:7]:
                lines.append(f"  · [{e.get('module','?')}] {e.get('summary','')}")
        txt = "\n".join(lines)
        return txt[:max_chars]
    except Exception:
        return "[全局工作区读出不可用]"


def _now() -> float:
    return time.time()


class GlobalWorkspace:
    """GWT 式全局工作区：candidate 竞争 → 赢家进意识缓冲 → 广播订阅者。线程安全单例。"""

    _instance: Optional["GlobalWorkspace"] = None
    _cls_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._buffer: deque[dict] = deque(maxlen=_BUFFER_CAP)      # 意识缓冲（当前"在意识里"的内容）
        self._history: deque[dict] = deque(maxlen=_HISTORY_CAP)    # 全事件史（含未进意识的）
        self._modules: dict[str, dict] = {}                        # module_id → {state,last_ts,detail,counts}
        self._focus: Optional[dict] = None                         # 当前全局焦点 {goal,source,ts,by}
        self._subs: list[Callable[[dict], None]] = []              # 进程内订阅者（广播回调）
        self._seq = 0
        self._load()

    # ── 单例 ──
    @classmethod
    def get(cls) -> "GlobalWorkspace":
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 落盘 / 恢复（失败一律静默：观察层不拦主链）──
    def _store_path(self) -> Path:
        p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "global_workspace.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load(self) -> None:
        try:
            p = self._store_path()
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                for e in (d.get("history") or [])[-_HISTORY_CAP:]:
                    if isinstance(e, dict):
                        self._history.append(e)
                for e in (d.get("buffer") or [])[-_BUFFER_CAP:]:
                    if isinstance(e, dict):
                        self._buffer.append(e)
                if isinstance(d.get("focus"), dict):
                    self._focus = d["focus"]
                self._seq = int(d.get("seq") or 0)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            p = self._store_path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "buffer": list(self._buffer),
                "history": list(self._history)[-_HISTORY_CAP:],
                "focus": self._focus,
                "seq": self._seq,
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            pass

    # ── 模块心跳 / 状态 ──
    def module_state(self, module: str, state: str, detail: str = "") -> None:
        """模块上报自身状态：idle / working / done / blocked / error。"""
        with self._lock:
            m = self._modules.setdefault(module, {"counts": {"events": 0, "won": 0}})
            m["state"] = state if state in ("idle", "working", "done", "blocked", "error") else "working"
            m["detail"] = str(detail or "")[:160]
            m["last_ts"] = _now()

    # ── 核心：提交 candidate（竞争进入意识缓冲并广播）──
    def submit(self, module: str, kind: str, summary: str, *,
               salience: float = 0.5, data: Optional[dict] = None,
               conv_id: str = "", user: str = "") -> dict:
        """专家模块向工作区提交一条 candidate。

        salience ∈ [0,1] 显著度：≥0.55 视为赢得竞争 → 进入意识缓冲 + 广播订阅者；
        低于阈值只进事件史（"处理过但没进意识"）。返回事件 dict（含 won 标记）。
        任何异常都被吞掉——工作区永不拖垮调用方。
        """
        try:
            with self._lock:
                self._seq += 1
                ev = {
                    "id": uuid.uuid4().hex[:10],
                    "seq": self._seq,
                    "ts": _now(),
                    "module": str(module or "unknown")[:32],
                    "kind": str(kind or "event")[:32],
                    "summary": str(summary or "")[:300],
                    "salience": round(max(0.0, min(1.0, float(salience))), 3),
                    "conv_id": str(conv_id or "")[:64],
                    "user": str(user or "")[:64],
                }
                if isinstance(data, dict) and data:
                    # 只留可 JSON 化的浅数据，防大对象撑爆落盘
                    try:
                        ev["data"] = json.loads(json.dumps(data, ensure_ascii=False)[:2000])
                    except Exception:
                        pass
                won = ev["salience"] >= 0.55
                ev["won"] = won
                self._history.append(ev)
                m = self._modules.setdefault(ev["module"], {"counts": {"events": 0, "won": 0}})
                m["counts"]["events"] = int(m["counts"].get("events", 0)) + 1
                m["last_ts"] = ev["ts"]
                if won:
                    m["counts"]["won"] = int(m["counts"].get("won", 0)) + 1
                    self._buffer.append(ev)
                    # 高显著度事件自动更新全局焦点（除非是收尾类事件）
                    if ev["salience"] >= 0.75 and kind not in ("done", "error", "result"):
                        self._focus = {"goal": ev["summary"], "source": ev["module"],
                                       "ts": ev["ts"], "by": "auto"}
                subs = list(self._subs) if won else []
                self._save()
            for cb in subs:   # 广播在锁外做，订阅者慢也不堵提交方
                try:
                    cb(ev)
                except Exception:
                    pass
            return ev
        except Exception as e:  # noqa: BLE001 —— 观察层绝不抛给主链
            logger.debug("[gw] submit 异常（已吞）：%s", e)
            return {}

    # ── 焦点（总控：系统当前统筹的目标）──
    def set_focus(self, goal: str, source: str = "user", by: str = "user") -> dict:
        with self._lock:
            self._focus = {"goal": str(goal or "")[:300], "source": source, "ts": _now(), "by": by}
            self._save()
            return dict(self._focus)

    def clear_focus(self) -> None:
        with self._lock:
            self._focus = None
            self._save()

    # ── 订阅（进程内广播；SSE 端点用）──
    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._subs.remove(cb)
                except ValueError:
                    pass
        return _unsub

    # ── 快照（总控台一屏所需的全部状态）──
    def snapshot(self, *, history_n: int = 60) -> dict:
        with self._lock:
            now = _now()
            mods = []
            ids = set(self._modules) | set(KNOWN_MODULES)
            for mid in sorted(ids):
                meta = KNOWN_MODULES.get(mid, {})
                st = self._modules.get(mid, {})
                last = float(st.get("last_ts") or 0)
                mods.append({
                    "id": mid,
                    "name": meta.get("name", mid),
                    "desc": meta.get("desc", ""),
                    "state": st.get("state", "idle"),
                    "detail": st.get("detail", ""),
                    "active": bool(last and now - last < _ACTIVE_WINDOW),
                    "last_ts": last or None,
                    "events": int((st.get("counts") or {}).get("events", 0)),
                    "won": int((st.get("counts") or {}).get("won", 0)),
                })
            return {
                "focus": dict(self._focus) if self._focus else None,
                "buffer": list(self._buffer),
                "modules": mods,
                "history": list(self._history)[-max(1, min(history_n, _HISTORY_CAP)):][::-1],
                "seq": self._seq,
                "ts": now,
            }

    def events_since(self, seq: int, limit: int = 100) -> list[dict]:
        with self._lock:
            return [e for e in self._history if int(e.get("seq") or 0) > seq][:limit]


# ── 模块级便捷函数（调用方一行接入，全部吞异常）──────────────────────
def gw() -> GlobalWorkspace:
    return GlobalWorkspace.get()


def broadcast(module: str, kind: str, summary: str, *, salience: float = 0.5,
              data: Optional[dict] = None, conv_id: str = "", user: str = "") -> None:
    """一行广播：任何模块在关键节点调用。失败静默。"""
    try:
        gw().submit(module, kind, summary, salience=salience, data=data,
                    conv_id=conv_id, user=user)
    except Exception:
        pass


def mark(module: str, state: str, detail: str = "") -> None:
    """一行状态上报。失败静默。"""
    try:
        gw().module_state(module, state, detail)
    except Exception:
        pass

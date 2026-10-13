"""MCP Atlas —— MCP 协议工具调用基准（V314 接入位）。

2026 新晋榜单：衡量模型经 MCP（Model Context Protocol）调用真实工具面的能力。
本机的 MCP 面已就绪（hashmm/api/routes/mcp_server.py 暴露 26 个工具）；官方
数据集与判分脚本以官方发布为准——本模块提供【能力预检 + 数据集接入位】，
数据集落地前不产生可对标分数（工具调用能力当前可用 BFCL 分数横向参考）。
"""
from __future__ import annotations

from pathlib import Path

from ._paths import bench_home


def data_dir() -> Path:
    return bench_home() / "mcp-atlas"


def detect() -> dict:
    have = data_dir().is_dir() and any(data_dir().rglob("*.json*"))
    # 能力预检：本机 MCP 服务面是否可导入（不起服务，只验模块与工具注册）
    mcp_ok, n_tools = False, 0
    try:
        from hashmm.api.tool_registry import TOOL_ANNOTATIONS
        n_tools = len(TOOL_ANNOTATIONS)
        mcp_ok = n_tools > 0
    except Exception:  # noqa: BLE001
        pass
    return {"installed": have and mcp_ok, "has_data": have, "mcp_ok": mcp_ok,
            "n_tools": n_tools,
            "hint": "" if have else "MCP Atlas 数据集未落地（官方发布后放入 bench_home()/mcp-atlas/）"}


def run(adapter, *, limit: int = 10) -> dict:
    det = detect()
    cap = f"本机 MCP 工具面预检：{'✓' if det['mcp_ok'] else '✗'}（{det['n_tools']} 个已注册工具）"
    if not det["has_data"]:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": (f"MCP Atlas 接入位已就绪。{cap}。数据集/官方判分脚本待官方发布——"
                           f"发布后放入 {data_dir()}/ 即接得上；当前工具调用能力可参考 BFCL 分数。")}
    return {"kind": "official", "skip": True, "score_pct": None,
            "detail": f"MCP Atlas 数据已就位（{cap}），判分脚本按官方口径接线中——不产生非官方假分。"}

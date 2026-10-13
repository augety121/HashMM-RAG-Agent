"""hashmm/tools/mcp_stdio.py — MCP stdio 桥（V76，Marvis 的 MarvisMCP.exe 同款形态）。

Claude Code / Codex / Cursor 等 MCP 客户端最通用的接入方式是 stdio
（stdin/stdout JSON-RPC，逐行）。本桥把 stdio 流量转发到 HashMM 后端的
HTTP /mcp 端点（hashmm/api/routes/mcp_server.py），让任何 MCP 客户端
把你的知识库检索 + GraphRAG 当工具调：

    # Claude Code 一行接入：
    claude mcp add hashmm -- python -m hashmm.tools.mcp_stdio \\
        --backend http://111.115.7.14:20014 --token <HASHMM_MCP_TOKEN>

    #（后端需开 HASHMM_MCP_SERVER=1；token 与后端 HASHMM_MCP_TOKEN 一致，
    #  或后端设 HASHMM_MCP_PUBLIC=1 免 token）

设计（铁律）：
- 纯标准库（urllib），零新增依赖；
- JSON-RPC 规范：notification（无 id）转发后不回写 stdout；
- 永不退出循环：坏 JSON 行回 parse error，后端不可达回 -32002 错误响应
  （客户端能看到失败原因，而不是桥僵死）；
- 核心逻辑 bridge_line() 为纯函数（注入 post_fn），可单测。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def post_json(backend: str, token: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST 单条 JSON-RPC 到后端 /mcp；网络/HTTP 错误抛异常（由调用方包成 RPC error）。"""
    url = backend.rstrip("/") + "/mcp"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bridge_line(line: str, post_fn) -> str | None:
    """处理一行 stdio 输入 → 返回要写回 stdout 的一行（None=不回写）。

    纯函数：post_fn(payload: dict) -> dict 由调用方注入（生产=post_json，测试=stub）。
    """
    line = (line or "").strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except Exception:
        return json.dumps({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}},
                          ensure_ascii=False)
    is_notification = isinstance(payload, dict) and payload.get("id") is None
    try:
        result = post_fn(payload)
    except Exception as e:
        if is_notification:
            return None                      # notification 失败也不回写（规范）
        return json.dumps({"jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
                           "error": {"code": -32002,
                                     "message": f"backend unreachable: {type(e).__name__}: {str(e)[:120]}"}},
                          ensure_ascii=False)
    if is_notification:
        return None                          # JSON-RPC 规范：notification 不回响应
    return json.dumps(result, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="HashMM MCP stdio 桥（接 Claude Code 等 MCP 客户端）")
    ap.add_argument("--backend", default=os.environ.get("HASHMM_BACKEND", "http://127.0.0.1:6006"),
                    help="HashMM 后端地址（默认 $HASHMM_BACKEND 或 http://127.0.0.1:6006）")
    ap.add_argument("--token", default=os.environ.get("HASHMM_MCP_TOKEN", ""),
                    help="后端 HASHMM_MCP_TOKEN（后端 PUBLIC 模式可省）")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    def _post(payload: dict) -> dict:
        return post_json(args.backend, args.token, payload, timeout=args.timeout)

    # stdio 主循环：逐行读 → 桥接 → 逐行写。stdout 只写 JSON-RPC，日志走 stderr。
    print(f"[hashmm-mcp-stdio] bridging to {args.backend}/mcp", file=sys.stderr, flush=True)
    for raw in sys.stdin:
        out = bridge_line(raw, _post)
        if out is not None:
            print(out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

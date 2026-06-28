# 把 HashMM 知识库接给 Claude Code / Codex（MCP）

HashMM 可以作为标准 MCP server 对外——别人的 Agent 直接调你的知识库检索 + GraphRAG，
数据不出你的机器（Marvis 的 MarvisMCP.exe 同款形态：stdio JSON-RPC）。

## 1. 后端开启 MCP server（默认关，零暴露面）

启动命令加两个环境变量：
```bash
HASHMM_MCP_SERVER=1 HASHMM_MCP_TOKEN=换成你的密钥 \
  HASHMM_AGENT_TRACE=1 HASHMM_AGENT_STREAM=1 HASHMM_AGENT_DEADLINE_S=480 \
  HASHMM_EVAL_EXEC=1 CUDA_VISIBLE_DEVICES=0 \
  python -m uvicorn hashmm.api.server:app --host 0.0.0.0 --port 6006
```
探活：`curl /mcp` → `{"enabled": true, "tools": [...]}`

## 2. Claude Code 一行接入

```bash
claude mcp add hashmm -- python -m hashmm.tools.mcp_stdio \
  --backend  --token 你的密钥
```
（机器上需有 HashMM 代码库或仅拷贝 `hashmm/tools/mcp_stdio.py`——它是纯标准库单文件，
零依赖可独立运行。）

之后在 Claude Code 里直接说"用 hashmm 查一下 XXX"，它会调用：
- `kb_search(query, top_k)` — 混合检索（向量+BM25+RRF+重排）
- `kg_query(query, mode)` — 知识图谱检索（local/mix/global）
- `corpus_stats()` — 语料/图谱规模

## 3. 安全边界

- 默认关闭；开了也只暴露**只读**工具（绝无写/删/admin）；
- 鉴权三选一：Bearer token（推荐）/ admin 会话 / 显式 HASHMM_MCP_PUBLIC=1；
- stdio 桥永不僵死：后端不可达回 JSON-RPC 错误，客户端能看到原因。

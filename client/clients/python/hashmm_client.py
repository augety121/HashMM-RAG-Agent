"""HashMM Python client —— 对外 /v1 API 的薄封装（对标 R2R 的 client.rag）。

让开发者像用 R2R 一样集成 HashMM：

    from hashmm_client import HashMMClient
    client = HashMMClient("http://你的IP:6006", api_key="你的key")

    # 纯检索
    hits = client.search("小米2024营收", top_k=3)

    # 检索增强生成（带来源）
    resp = client.rag("小米2024营收多少？")
    print(resp["answer"])
    for s in resp["sources"]:
        print(s["filename"], s["page"])

纯 stdlib（urllib），零第三方依赖，可直接拷进任何项目使用。
需服务端开启：HASHMM_PUBLIC_API=1 HASHMM_API_KEY=你的key。
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error


class HashMMClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except Exception as e:
            return {"error": str(e)}

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except Exception as e:
            return {"error": str(e)}

    # ── 公开方法 ──
    def info(self) -> dict:
        """服务能力/版本描述。"""
        return self._get("/v1/info")

    def search(self, query: str, top_k: int = 5) -> dict:
        """混合检索（向量+BM25+RRF+重排）。返回 {query, results, num_results}。"""
        return self._post("/v1/search", {"query": query, "top_k": top_k})

    def rag(self, query: str, top_k: int = 5, history: list | None = None) -> dict:
        """检索增强生成。返回 {query, answer, sources, num_sources}。"""
        return self._post("/v1/rag", {"query": query, "top_k": top_k, "history": history or []})


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:6006"
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    c = HashMMClient(base, api_key=key)
    print("info:", json.dumps(c.info(), ensure_ascii=False, indent=2))
    print("rag :", json.dumps(c.rag("小米2024营收多少"), ensure_ascii=False, indent=2)[:800])

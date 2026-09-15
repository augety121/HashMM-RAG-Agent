from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from hashmm.search_integrations import get_search_integration

ProviderFn = Callable[[str, int, dict[str, Any], dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    label: str
    kind: str
    fn: ProviderFn


def _post(url: str, body: dict, headers: dict[str, str], timeout: int = 18) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "HashMM-Retrieval-Fabric/1.0", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try: raw = response.read(4 * 1024 * 1024 + 1)
        except TypeError: raw = response.read()  # simple test doubles / legacy clients
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("provider_response_too_large")
    value = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(value, dict):
        raise RuntimeError("provider_response_invalid")
    return value


def _get(url: str, headers: dict[str, str], timeout: int = 18) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "HashMM-Retrieval-Fabric/1.0", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try: raw = response.read(4 * 1024 * 1024 + 1)
        except TypeError: raw = response.read()
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("provider_response_too_large")
    value = json.loads(raw.decode("utf-8", errors="replace"))
    return value if isinstance(value, dict) else {}


def _credentials(owner_id: str, provider: str) -> tuple[str, dict[str, Any]]:
    item = get_search_integration(owner_id, provider, include_secret=True) if owner_id else None
    if item and item.get("enabled") and item.get("api_key"):
        return str(item["api_key"]), dict(item.get("config") or {})
    setting_map = {"serper": "serper_api_key", "tavily": "tavily_api_key",
                   "baidu": "baidu_search_api_key", "brave": "brave_search_api_key",
                   "exa": "exa_api_key", "gemini": "gemini_api_key"}
    env_map = {
        "baidu": "HASHMM_BAIDU_SEARCH_API_KEY", "brave": "HASHMM_BRAVE_SEARCH_API_KEY",
        "exa": "HASHMM_EXA_API_KEY", "gemini": "HASHMM_GEMINI_API_KEY",
        "serper": "HASHMM_SERPER_API_KEY", "tavily": "HASHMM_TAVILY_API_KEY",
    }
    key = ""
    if provider in setting_map:
        try:
            from hashmm.api.settings_store import get_setting
            key = str(get_setting(setting_map[provider]) or "")
        except Exception:
            key = ""
    return key or os.environ.get(env_map.get(provider, ""), ""), {}


def configured(owner_id: str, provider: str) -> bool:
    if provider == "duckduckgo":
        return True
    if provider == "doubao":
        item = get_search_integration(owner_id, provider, include_secret=True) if owner_id else None
        return bool(item and item.get("enabled") and item.get("api_key"))
    return bool(_credentials(owner_id, provider)[0])


def _serper(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    data = _post("https://google.serper.dev/search", {"q": query, "num": count, "hl": options.get("locale", "zh-cn")},
                 {"X-API-KEY": auth["key"]})
    return [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", ""),
             "published_at": r.get("date")} for r in (data.get("organic") or [])[:count] if isinstance(r, dict)]


def _tavily(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    body = {"api_key": auth["key"], "query": query, "max_results": count,
            "search_depth": "advanced" if options.get("mode") == "deep" else "basic"}
    if options.get("freshness_days"):
        body["days"] = options["freshness_days"]
    data = _post("https://api.tavily.com/search", body, {})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", ""),
             "provider_score": r.get("score"), "published_at": r.get("published_date")}
            for r in (data.get("results") or [])[:count] if isinstance(r, dict)]


def _brave(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    params = {"q": query, "count": count, "search_lang": str(options.get("locale", "zh-CN")).split("-")[0]}
    if options.get("freshness_days"):
        params["freshness"] = f"p{int(options['freshness_days'])}d"
    data = _get("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params),
                {"Accept": "application/json", "X-Subscription-Token": auth["key"]})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", ""),
             "published_at": r.get("age") or r.get("page_age")}
            for r in ((data.get("web") or {}).get("results") or [])[:count] if isinstance(r, dict)]


def _exa(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    body: dict[str, Any] = {"query": query, "numResults": count, "contents": {"highlights": {"maxCharacters": 1200}}}
    if options.get("freshness_days"):
        from datetime import datetime, timedelta, timezone
        body["startPublishedDate"] = (datetime.now(timezone.utc) - timedelta(days=int(options["freshness_days"]))).isoformat()
    data = _post("https://api.exa.ai/search", body, {"x-api-key": auth["key"]})
    result = []
    for row in (data.get("results") or [])[:count]:
        if not isinstance(row, dict): continue
        highlights = row.get("highlights") or []
        result.append({"title": row.get("title", ""), "url": row.get("url", ""),
                       "snippet": "\n".join(map(str, highlights)) or row.get("text", ""),
                       "published_at": row.get("publishedDate"), "provider_score": row.get("score")})
    return result


def _baidu(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    data = _post("https://qianfan.baidubce.com/v2/ai_search/web_search",
                 {"query": query, "search_recency_filter": "noLimit", "search_result_count": count},
                 {"Authorization": f"Bearer {auth['key']}"})
    rows = data.get("references") or data.get("results") or data.get("web_search_results") or []
    result = []
    for row in rows[:count] if isinstance(rows, list) else []:
        if not isinstance(row, dict): continue
        result.append({"title": row.get("title") or row.get("name") or "", "url": row.get("url") or row.get("link") or "",
                       "snippet": row.get("snippet") or row.get("summary") or row.get("content") or "",
                       "published_at": row.get("date") or row.get("publish_time")})
    return result


def _gemini(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    model = str(auth.get("config", {}).get("model") or "gemini-2.5-flash")
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    data = _post(url, {"model": model, "input": query, "tools": [{"type": "google_search"}]},
                 {"x-goog-api-key": auth["key"]})
    # Interactions responses may expose citations in annotations or grounding
    # chunks depending on model revision. Traverse bounded JSON and retain only
    # objects that contain an HTTP URL.
    found: list[dict] = []
    def walk(value: Any, depth: int = 0) -> None:
        if depth > 8 or len(found) >= count: return
        if isinstance(value, dict):
            url_value = value.get("url") or value.get("uri")
            if isinstance(url_value, str) and url_value.startswith(("http://", "https://")):
                found.append({"title": value.get("title") or value.get("name") or "Google Search citation",
                              "url": url_value, "snippet": value.get("snippet") or value.get("text") or ""})
            for child in list(value.values())[:80]: walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:100]: walk(child, depth + 1)
    walk(data)
    return found[:count]


def _doubao(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    cfg = auth["config"]
    version = str(cfg.get("version") or "global")
    base = str(cfg.get("base_url") or "https://open.feedcoopapi.com/search_api").rstrip("/")
    if version == "custom":
        endpoint = base + "/web_search"; body: dict[str, Any] = {"Query": query, "SearchType": "web", "Count": count}
        if cfg.get("auth_level") is not None: body["Filter"] = {"AuthInfoLevel": cfg["auth_level"]}
    else:
        endpoint = base + "/global_search"; body = {"query": query, "doc_count": count,
            "max_snippet_length": int(cfg.get("snippet_length") or 800), "max_image_count_per_doc": 0}
    data = _post(endpoint, body, {"Authorization": f"Bearer {auth['key']}"})
    error = (data.get("ResponseMetadata") or {}).get("Error")
    if error: raise RuntimeError("provider_error")
    rows = ((data.get("Result") or {}).get("WebResults" if version == "custom" else "Documents") or [])[:count]
    result = []
    for row in rows:
        if not isinstance(row, dict): continue
        snippets = row.get("Snippet") or []
        snippet = row.get("Content") or row.get("Summary") or ""
        if isinstance(snippets, list): snippet = "\n".join(str(x.get("Text") or "") for x in snippets if isinstance(x, dict))
        result.append({"title": row.get("Title", ""), "url": row.get("Url", ""), "snippet": snippet,
                       "published_at": row.get("PublishTime") or (row.get("DocumentInfo") or {}).get("PublishTime")})
    return result


def _duckduckgo(query: str, count: int, auth: dict, options: dict) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                    for r in ddgs.text(query, max_results=count)]
    except ImportError:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            page = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</', page, re.DOTALL)
        clean = lambda value: re.sub(r"<[^>]+>", "", value).strip()
        return [{"title": clean(title), "url": href, "snippet": clean(snippets[i]) if i < len(snippets) else ""}
                for i, (href, title) in enumerate(titles[:count])]


PROVIDERS: dict[str, ProviderSpec] = {
    "doubao": ProviderSpec("doubao", "豆包搜索兼容接口（Beta）", "raw_search", _doubao),
    "baidu": ProviderSpec("baidu", "百度千帆 AI Search", "raw_search", _baidu),
    "brave": ProviderSpec("brave", "Brave Search", "raw_search", _brave),
    "exa": ProviderSpec("exa", "Exa", "neural_search", _exa),
    "gemini": ProviderSpec("gemini", "Google Search Grounding", "managed_grounding", _gemini),
    "serper": ProviderSpec("serper", "Serper Google results", "aggregator", _serper),
    "tavily": ProviderSpec("tavily", "Tavily", "agent_search", _tavily),
    "duckduckgo": ProviderSpec("duckduckgo", "DuckDuckGo fallback", "fallback", _duckduckgo),
}


def search(provider: str, owner_id: str, query: str, count: int, options: dict[str, Any]) -> list[dict[str, Any]]:
    spec = PROVIDERS[provider]
    key, config = _credentials(owner_id, provider) if provider != "doubao" else ("", {})
    if provider == "doubao":
        item = get_search_integration(owner_id, provider, include_secret=True)
        key, config = (str(item.get("api_key") or ""), dict(item.get("config") or {})) if item else ("", {})
    if provider != "duckduckgo" and not key:
        raise LookupError("provider_not_configured")
    return spec.fn(query, count, {"key": key, "config": config}, options)

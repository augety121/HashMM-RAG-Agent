"""WebArena canonical tasks driven by HashMM's browser agent.

The evaluator runs against the exact Playwright page used by the agent.  The
website environment is external state: GitHub-hosted runners execute the agent
and evaluator, while a canonical WebArena AMI (or equivalent self-hosted site
stack) supplies the seven URLs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

from ._paths import bench_home


SITE_ENVS = (
    "SHOPPING", "SHOPPING_ADMIN", "REDDIT", "GITLAB", "MAP", "WIKIPEDIA", "HOMEPAGE",
)
# HOMEPAGE is retained for compatibility with the canonical env_config, but no
# released task references it; its optional landing service must not block 812-task evaluation.
TASK_SITE_ENVS = SITE_ENVS[:-1]
_AUTH_ACCOUNTS = {
    "reddit": {"username": "MarvelsGrantMan136", "password": "test1234"},
    "gitlab": {"username": "byteblaze", "password": "hello1234"},
    "shopping": {"username": "emma.lopez@gmail.com", "password": "Password.123"},
    "shopping_admin": {"username": "admin", "password": "admin1234"},
    "shopping_site_admin": {"username": "admin", "password": "admin1234"},
}
_OFFICIAL_JUDGE_MODELS = {"gpt-4-1106-preview", "gpt-4-turbo", "gpt-4-turbo-preview"}


def repo_dir() -> Path:
    return bench_home() / "webarena"


def raw_config_path() -> Path:
    return repo_dir() / "config_files" / "test.raw.json"


def _raw_records() -> list[dict]:
    raw = raw_config_path()
    if raw.is_file():
        try:
            data = json.loads(raw.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
        except Exception:  # noqa: BLE001
            pass
    # Compatibility with an already generated official checkout.
    rows: list[dict] = []
    for path in sorted((repo_dir() / "config_files").glob("[0-9]*.json"),
                       key=lambda p: int(p.stem) if p.stem.isdigit() else 10**9):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(row, dict):
                rows.append(row)
        except Exception:  # noqa: BLE001
            continue
    return rows


def _derive_site_urls(host: str) -> dict[str, str]:
    value = (host or "").strip().rstrip("/")
    if not value:
        return {}
    parsed = urlsplit(value if "://" in value else "http://" + value)
    hostname = parsed.hostname or ""
    if not hostname:
        return {}
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    origin = f"{parsed.scheme or 'http'}://{hostname}"
    return {
        "SHOPPING": origin + ":7770",
        "SHOPPING_ADMIN": origin + ":7780/admin",
        "REDDIT": origin + ":9999",
        "GITLAB": origin + ":8023",
        "MAP": origin + ":3000",
        "WIKIPEDIA": origin + ":8888/wikipedia_en_all_maxi_2022-05/A/User:The_other_Kiwix_guy/Landing",
        "HOMEPAGE": origin + ":4399",
    }


def site_urls() -> dict[str, str]:
    """Read explicit URLs, with one-host derivation as the convenient CI path."""
    out = _derive_site_urls(os.environ.get("HASHMM_WEBARENA_HOST", ""))
    for key in SITE_ENVS:
        value = (os.environ.get(f"HASHMM_WEBARENA_{key}") or os.environ.get(key) or "").strip()
        if value:
            out[key] = value.rstrip("/")
    return out


def detect() -> dict:
    records = _raw_records()
    urls = site_urls()
    missing = [key for key in TASK_SITE_ENVS if key not in urls]
    installed = len(records) == 812 and bool(urls)
    if not records:
        hint = "WebArena 官方 test.raw.json 未安装"
    elif len(records) != 812:
        hint = f"WebArena 官方任务数异常：期望 812，实际 {len(records)}"
    elif not urls:
        hint = ("未配置 WebArena 站点。设置一个 HASHMM_WEBARENA_HOST（官方 AMI 的公网域名/IP，"
                "不要带端口）即可自动派生全部七个 URL")
    else:
        hint = "" if not missing else f"缺少站点 URL：{missing}"
    return {
        "installed": installed, "has_config": len(records) == 812,
        "urls": urls, "missing": missing, "n_tasks": len(records), "hint": hint,
    }


def _auto_install_tasks() -> tuple[bool, str]:
    from .swebench_local import _git_mirrors

    destination = repo_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    timeout = int(os.environ.get("HASHMM_GIT_CLONE_TIMEOUT", "600"))
    last = ""
    for url in _git_mirrors("web-arena-x/webarena"):
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(destination)],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            continue
        if proc.returncode == 0 and len(_raw_records()) == 812:
            return True, f"812 道官方任务已自动安装（来源 {url}）"
        last = (proc.stderr or proc.stdout or "官方任务数不是 812")[:200]
    return False, f"WebArena 自动安装失败：{last}"


SETUP_GUIDE = """WebArena 的评测端已经接通，但官方网站环境必须独立托管。

最省事的官方路径：在 AWS us-east-2 启动公共 AMI
`webarena-with-configurable-map-backend`（ami-08a862bf98e3bd7aa），启动并配置网站后，
在 Actions 的 `webarena_host` 输入框填该实例公网域名/IP（不带端口）。代码会自动派生
7770/7780/9999/8023/3000/8888/4399 七个站点地址、健康检查并生成登录 cookie。

GitHub hosted runner 只适合跑 Agent/Chromium/evaluator；官方网站镜像需要大容量持久磁盘，
不能在每次 hosted runner 临时任务里可靠地重新下载和启动。"""


def _resolve_obj(value: Any, urls: dict[str, str]) -> Any:
    if isinstance(value, str):
        for key, url in urls.items():
            value = value.replace(f"__{key}__", url)
        return value
    if isinstance(value, list):
        return [_resolve_obj(item, urls) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_obj(item, urls) for key, item in value.items()}
    return value


def resolve_placeholders(value: str, urls: dict[str, str]) -> str:
    """Public compatibility helper for resolving canonical ``__SITE__`` URLs."""
    return str(_resolve_obj(value, urls))


def _probe_one(key: str, url: str) -> dict:
    target = url
    if key == "SHOPPING_ADMIN" and not target.rstrip("/").endswith("/admin"):
        target = target.rstrip("/") + "/admin"
    request = Request(target, headers={"User-Agent": "HashMM-WebArena-Preflight/1"})
    started = time.time()
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - user-owned benchmark host
            status = int(getattr(response, "status", 200))
        ok = status < 500
        error = ""
    except HTTPError as exc:
        status = int(exc.code)
        ok = status < 500
        error = "" if ok else f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001
        status = 0
        ok = False
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {
        "site": key, "url": target, "ok": ok, "http_status": status,
        "elapsed_ms": int((time.time() - started) * 1000), "error": error,
    }


def probe_sites(urls: dict[str, str]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(7, max(1, len(urls)))) as pool:
        futures = {pool.submit(_probe_one, key, url): key for key, url in urls.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[key] = {"site": key, "url": urls[key], "ok": False,
                                "http_status": 0, "error": f"{type(exc).__name__}: {exc}"}
    return results


def _login_site(page, site: str, urls: dict[str, str]) -> None:
    account = _AUTH_ACCOUNTS[site]
    if site == "shopping":
        page.goto(urls["SHOPPING"] + "/customer/account/login/", wait_until="domcontentloaded")
        page.get_by_label("Email", exact=True).fill(account["username"])
        page.get_by_label("Password", exact=True).fill(account["password"])
        page.get_by_role("button", name="Sign In").click()
    elif site == "reddit":
        page.goto(urls["REDDIT"] + "/login", wait_until="domcontentloaded")
        page.get_by_label("Username").fill(account["username"])
        page.get_by_label("Password").fill(account["password"])
        page.get_by_role("button", name="Log in").click()
    elif site == "shopping_admin":
        page.goto(urls["SHOPPING_ADMIN"], wait_until="domcontentloaded")
        page.get_by_placeholder("user name").fill(account["username"])
        page.get_by_placeholder("password").fill(account["password"])
        page.get_by_role("button", name="Sign in").click()
    elif site == "gitlab":
        page.goto(urls["GITLAB"] + "/users/sign_in", wait_until="domcontentloaded")
        page.get_by_test_id("username-field").fill(account["username"])
        page.get_by_test_id("password-field").fill(account["password"])
        page.get_by_test_id("sign-in-button").click()
    else:
        raise ValueError(f"未知登录站点：{site}")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        page.wait_for_timeout(1500)


def prepare_auth_states(tasks: list[dict], urls: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Regenerate the exact official storage-state files required by selected tasks."""
    required: set[str] = set()
    for task in tasks:
        storage = str(task.get("storage_state") or "").strip()
        if storage:
            required.add(Path(storage).name)
    if not required:
        return {}, {}

    auth_dir = repo_dir() / ".auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    states: dict[str, str] = {}
    errors: dict[str, str] = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                for filename in sorted(required):
                    sites = filename.removesuffix("_state.json").split(".")
                    if not all(site.upper() in urls for site in sites):
                        errors[filename] = f"登录所需站点未配置：{sites}"
                        continue
                    context = browser.new_context(viewport={"width": 1280, "height": 900})
                    page = context.new_page()
                    page.set_default_timeout(30000)
                    try:
                        for site in sites:
                            _login_site(page, site, urls)
                        destination = auth_dir / filename
                        context.storage_state(path=str(destination))
                        states[filename] = str(destination.resolve())
                    except Exception as exc:  # noqa: BLE001
                        errors[filename] = f"{type(exc).__name__}: {str(exc)[:240]}"
                    finally:
                        context.close()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        message = f"Chromium/Playwright 启动失败：{type(exc).__name__}: {str(exc)[:240]}"
        for filename in required:
            errors.setdefault(filename, message)
    return states, errors


def load_tasks(limit: int, urls: dict[str, str],
               reachable: set[str] | None = None) -> list[dict]:
    if reachable is None:
        reachable = set(urls)
    out: list[dict] = []
    for raw in _raw_records():
        task = _resolve_obj(raw, urls)
        sites = [str(site).upper() for site in (task.get("sites") or [])]
        if sites and not all(site in urls and site in reachable for site in sites):
            continue
        task["sites"] = sites
        task.setdefault("id", task.get("task_id"))
        out.append(task)
        if limit and len(out) >= limit:
            break
    return out


def clean_answer(value: str) -> str:
    answer = (value or "").strip()
    if len(answer) >= 2 and answer[0] == answer[-1] and answer[0] in {"'", '"'}:
        answer = answer[1:-1]
    return answer.lower()


def _must_include(reference: str, prediction: str, *, tokenize: bool) -> bool:
    ref = clean_answer(reference)
    pred = clean_answer(prediction)
    if tokenize and len(ref) == 1:
        return ref in re.findall(r"\w+|[^\w\s]", pred, flags=re.UNICODE)
    return ref in pred


def _judge(adapter, prompt: str, *, positive: str, negatives: tuple[str, ...]) -> bool:
    response = clean_answer(adapter.answer(prompt))
    if any(word in response for word in negatives):
        return False
    return positive in response


def eval_string_match(adapter, answer: str | dict | None = None,
                      config: dict | None = None):
    """Evaluate WebArena string answers.

    The canonical runner calls ``(adapter, answer, full_config)`` and receives
    ``(float_score, note, used_judge)``. The former public helper accepted
    ``(answer, reference_answers)``; keep that deterministic two-value form for
    downstream users, while refusing to fake fuzzy-judge success without an LLM.
    """
    legacy = config is None
    if legacy:
        legacy_answer = str(adapter or "")
        references = answer if isinstance(answer, dict) else {}
        adapter = None
        answer = legacy_answer
        config = {"eval": {"reference_answers": references}}
    assert config is not None
    answer = str(answer or "")
    refs = (config.get("eval") or {}).get("reference_answers") or {}
    score = 1.0
    used_judge = False
    notes: list[str] = []
    for approach, value in refs.items():
        if approach == "exact_match":
            ok = clean_answer(str(value)) == clean_answer(answer)
            notes.append(f"exact_match={ok}")
            score *= float(ok)
        elif approach == "must_include":
            values = value if isinstance(value, list) else [value]
            for reference in values:
                ok = _must_include(str(reference), answer, tokenize=len(values) == 1)
                notes.append(f"must_include({reference!r})={ok}")
                score *= float(ok)
        elif approach == "fuzzy_match":
            if legacy:
                return None, "fuzzy_match 需要 LLM 裁判，纯规则模式不评分"
            used_judge = True
            if value == "N/A":
                ok = clean_answer(answer) == "n/a"
                if not ok:
                    prompt = (
                        f"task: {config.get('intent', '')}\n"
                        f"actual unachievable reason: {(config.get('eval') or {}).get('string_note', '')}\n"
                        f"reported unachievable reason: {answer}\n"
                        "Reply with exactly same or different."
                    )
                    ok = _judge(adapter, prompt, positive="same", negatives=("different",))
                notes.append(f"unachievable_match={ok}")
                score *= float(ok)
            else:
                values = value if isinstance(value, list) else [value]
                for reference in values:
                    prompt = (
                        "Grade whether the student answer is semantically equivalent to the reference.\n"
                        f"question: {config.get('intent', '')}\nreference answer: {reference}\n"
                        f"student answer: {answer}\nReply with exactly correct or incorrect."
                    )
                    ok = _judge(adapter, prompt, positive="correct",
                                negatives=("incorrect", "partially correct"))
                    notes.append(f"fuzzy_match={ok}")
                    score *= float(ok)
    note = "; ".join(notes)
    if legacy:
        return score == 1.0, note
    return score, note, used_judge


def eval_url_match(final_url: str, config: dict | str) -> tuple[float, str]:
    legacy = isinstance(config, str)
    if legacy:
        config = {"eval": {"reference_url": config}}
    eval_config = config.get("eval") or {}
    refs = [value.rstrip("/") for value in str(eval_config.get("reference_url") or "").split(" |OR| ")]
    prediction = str(final_url or "").rstrip("/")

    def parse_url(value: str) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(value)
        return parsed.netloc + parsed.path, parse_qs(parsed.query)

    ref_paths: list[str] = []
    ref_queries: dict[str, set[str]] = {}
    for reference in refs:
        path, query = parse_url(reference)
        ref_paths.append(path)
        for key, values in query.items():
            ref_queries.setdefault(key, set()).update(values)
    pred_path, pred_query = parse_url(prediction)
    base_ok = any(path in pred_path for path in ref_paths)
    query_ok = all(any(value in pred_query.get(key, []) for value in values)
                   for key, values in ref_queries.items())
    ok = base_ok and query_ok
    score = bool(ok) if legacy else float(ok)
    return score, f"url_match={ok}: expected={refs[:2]!r}, got={prediction[:180]!r}"


def _http_json(url: str, *, method: str = "GET", data: dict | None = None,
               headers: dict[str, str] | None = None) -> Any:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, method=method,
                      headers={"Content-Type": "application/json", **(headers or {})})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - canonical benchmark host
        return json.loads(response.read().decode("utf-8"))


def _shopping_token(urls: dict[str, str]) -> str:
    account = _AUTH_ACCOUNTS["shopping_site_admin"]
    return str(_http_json(
        urls["SHOPPING"] + "/rest/default/V1/integration/admin/token",
        method="POST", data=account,
    ))


def _shopping_latest_order_url(urls: dict[str, str]) -> str:
    params = {
        "searchCriteria[sortOrders][0][field]": "created_at",
        "searchCriteria[sortOrders][0][direction]": "DESC",
        "searchCriteria[pageSize]": "1",
    }
    data = _http_json(
        urls["SHOPPING"] + "/rest/V1/orders?" + urlencode(params),
        headers={"Authorization": f"Bearer {_shopping_token(urls)}"},
    )
    order_id = int(data["items"][0]["increment_id"])
    return f"{urls['SHOPPING']}/sales/order/view/order_id/{order_id}/"


def _shopping_review(urls: dict[str, str], sku: str, field: str) -> str:
    data = _http_json(
        f"{urls['SHOPPING']}/rest/V1/products/{quote(sku, safe='')}/reviews",
        headers={"Authorization": f"Bearer {_shopping_token(urls)}"},
    )
    if not data:
        return ""
    if field == "author":
        return str(data[-1]["nickname"])
    return str(data[-1]["ratings"][0]["percent"])


def _reddit_post_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    if len(parts) < 4 or parts[1] != "f":
        return url
    return f"{parsed.scheme}://{parsed.netloc}/f/{parts[2]}/{parts[3]}/"


def _program_target_url(raw: str, last_url: str, urls: dict[str, str]) -> str:
    if raw == "func:shopping_get_latest_order_url()":
        return _shopping_latest_order_url(urls)
    if raw.startswith("func:reddit_get_post_url("):
        return _reddit_post_url(last_url)
    raise ValueError(f"未知官方 target URL helper：{raw}")


def _program_locator(page, locator: str, urls: dict[str, str]) -> str:
    match = re.fullmatch(r"func:shopping_get_sku_latest_review_(author|rating)\('([^']+)'\)", locator)
    if match:
        return _shopping_review(urls, match.group(2), match.group(1))
    match = re.fullmatch(r"func:gitlab_get_project_memeber_role\(__page__, '([^']+)'\)", locator)
    if match:
        account = match.group(1)
        return str(page.evaluate(
            """(account) => {
              const rows = [...document.querySelectorAll("td[data-label='Account'] span.gl-avatar-labeled-sublabel")];
              const index = rows.findIndex(el => el.outerText === '@' + account);
              const roles = document.querySelectorAll('td.col-max-role span');
              return index >= 0 && roles[index] ? roles[index].outerText : '';
            }""", account
        ))
    raise ValueError(f"未知官方 locator helper：{locator}")


def eval_program_html(page, config: dict, urls: dict[str, str]) -> tuple[float, str]:
    score = 1.0
    notes: list[str] = []
    targets = (config.get("eval") or {}).get("program_html") or []
    for index, target in enumerate(targets):
        target_url = str(target.get("url") or "")
        if target_url.startswith("func:"):
            target_url = _program_target_url(target_url, page.url, urls)
        if target_url != "last":
            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        locator = str(target.get("locator") or "")
        if not locator.strip():
            selected = page.content()
        elif locator.startswith("document.") or locator.startswith("[...document."):
            for action in target.get("prep_actions") or []:
                try:
                    page.evaluate(f"() => {action}")
                except Exception:  # noqa: BLE001 - official evaluator ignores prep failures
                    pass
            try:
                selected = str(page.evaluate(f"() => {locator}") or "")
            except Exception:  # noqa: BLE001 - official evaluator treats missing DOM as empty
                selected = ""
        elif locator.startswith("func:"):
            selected = _program_locator(page, locator, urls)
        else:
            raise ValueError(f"未知官方 locator：{locator}")
        selected = html.unescape(selected)
        required = target.get("required_contents") or {}
        if "exact_match" in required:
            ok = clean_answer(str(required["exact_match"])) == clean_answer(selected)
            score *= float(ok)
        elif "must_include" in required:
            ok = True
            for content in required["must_include"]:
                options = str(content).split(" |OR| ")
                ok = ok and any(_must_include(option, selected, tokenize=False) for option in options)
            score *= float(ok)
        else:
            raise ValueError(f"未知 required_contents：{sorted(required)}")
        notes.append(f"program_html[{index}]={bool(score)}")
    return score, "; ".join(notes)


def evaluate_case(adapter, kernel, conv_id: str, answer: str,
                  config: dict, urls: dict[str, str]) -> tuple[float, list[str], bool, str]:
    score = 1.0
    notes: list[str] = []
    used_judge = False
    final_url = kernel.run_on_page(conv_id, lambda page: page.url)
    for eval_type in (config.get("eval") or {}).get("eval_types") or []:
        if eval_type == "string_match":
            current, note, judged = eval_string_match(adapter, answer, config)
            used_judge = used_judge or judged
        elif eval_type == "url_match":
            current, note = eval_url_match(final_url, config)
        elif eval_type == "program_html":
            current, note = kernel.run_on_page(
                conv_id, lambda page: eval_program_html(page, config, urls)
            )
        else:
            raise ValueError(f"官方 evaluator 类型不支持：{eval_type}")
        score *= float(current)
        notes.append(note)
    return score, notes, used_judge, str(final_url)


def _artifact_dir() -> Path | None:
    root = (os.environ.get("HASHMM_BENCH_ARTIFACT_DIR") or "").strip()
    return Path(root) / "diagnostics" / "webarena" if root else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(adapter, *, limit: int = 20) -> dict:
    det = detect()
    if not det["has_config"]:
        installed, note = _auto_install_tasks()
        det = detect()
        if not installed or not det["has_config"]:
            return {"kind": "official", "skip": True, "score_pct": None,
                    "pipeline_ok": False, "detail": f"{note}\n\n{SETUP_GUIDE}"}
    if not det["urls"]:
        return {"kind": "official", "skip": True, "score_pct": None,
                "pipeline_ok": False, "detail": f"{det['hint']}。\n\n{SETUP_GUIDE}"}

    try:
        from hashmm.tools.browser_kernel import get_kernel
        kernel = get_kernel()
        if not kernel.playwright_available():
            raise RuntimeError("playwright Python 包未安装")
    except Exception as exc:  # noqa: BLE001
        return {"kind": "official", "skip": True, "score_pct": None,
                "pipeline_ok": False,
                "detail": f"WebArena Chromium 前置失败：{type(exc).__name__}: {exc}"}

    artifact_dir = _artifact_dir()
    probes = probe_sites(det["urls"])
    if artifact_dir:
        _write_json(artifact_dir / "site-health.json", probes)
    reachable = {key for key, result in probes.items() if result.get("ok")}
    require_all = (os.environ.get("HASHMM_WEBARENA_REQUIRE_ALL") or "").lower() in {
        "1", "true", "yes", "on",
    }
    missing_or_down = [key for key in TASK_SITE_ENVS if key not in reachable]
    if require_all and missing_or_down:
        details = [f"{key}: {probes.get(key, {}).get('error') or '未配置'}" for key in missing_or_down]
        return {"kind": "official", "skip": True, "score_pct": None,
                "pipeline_ok": False,
                "detail": "WebArena 严格预检失败：" + "；".join(details) + "\n\n" + SETUP_GUIDE}

    from .sample_stats import MIN_COMPARABLE_N, format_breakdown, resolve_limit
    limit = resolve_limit("webarena", limit)
    tasks = load_tasks(limit, det["urls"], reachable)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "pipeline_ok": False,
                "detail": f"没有可运行任务；可达站点={sorted(reachable)}。\n\n{SETUP_GUIDE}"}

    states, auth_errors = prepare_auth_states(tasks, det["urls"])
    if artifact_dir:
        _write_json(artifact_dir / "auth-preflight.json", {
            "created": sorted(states), "errors": auth_errors,
        })
    unavailable_auth = sorted({
        Path(str(task.get("storage_state"))).name
        for task in tasks if task.get("storage_state")
        and Path(str(task.get("storage_state"))).name not in states
    })
    if unavailable_auth:
        return {"kind": "official", "skip": True, "score_pct": None,
                "pipeline_ok": False,
                "detail": ("WebArena 官方自动登录失败：" + "；".join(
                    f"{name}: {auth_errors.get(name, '未生成')}" for name in unavailable_auth
                ))}

    concurrency = max(1, min(
        int(os.environ.get("HASHMM_WEBARENA_CONCURRENCY", "2")), 4, len(tasks)
    ))
    task_timeout = max(60, int(os.environ.get("HASHMM_WEBARENA_TASK_TIMEOUT", "600")))
    max_iterations = max(5, int(os.environ.get("HASHMM_WEBARENA_MAX_STEPS", "30")))
    previous_allow = os.environ.get("HASHMM_BROWSER_ALLOW_PRIVATE")
    os.environ["HASHMM_BROWSER_ALLOW_PRIVATE"] = "1"
    artifact_lock = threading.Lock()

    from hashmm.tools import agent_bench as AB

    def run_one(config: dict) -> dict:
        task_id = str(config.get("task_id"))
        conv_id = f"webarena-{task_id}"
        storage = str(config.get("storage_state") or "").strip()
        storage_path = states.get(Path(storage).name) if storage else None
        started = time.time()
        case: dict[str, Any] = {
            "task_id": task_id, "sites": config.get("sites") or [],
            "eval_types": (config.get("eval") or {}).get("eval_types") or [],
            "storage_state": Path(storage_path).name if storage_path else None,
        }
        try:
            kernel.configure_session(conv_id, storage_state=storage_path)
            start_urls = [part.strip() for part in str(config.get("start_url") or "").split(" |AND| ")
                          if part.strip()]
            prompt = (
                "使用 browser_open/browser_read/browser_act 完成下面的 WebArena 任务。"
                "不要使用 web_search 替代站内操作。需要回答时，最终答案只写任务要求的内容。\n\n"
                f"起始页面：{start_urls}\n任务：{config.get('intent', '')}"
            )
            task = AB.Task(
                id=conv_id, category="webarena", turns=[prompt], requires=set(),
                scorers=[AB.answer_nonempty(min_len=0)], max_seconds=task_timeout,
                max_iterations=max_iterations, max_tool_calls=120, max_exec_calls=5,
            )
            result = AB.run_task(task, adapter.llm_fn, conv_id=conv_id,
                                 preserve_workspace=False, permission_mode="bypass")
            answer = str(result.get("answer") or "")
            score, notes, used_judge, final_url = evaluate_case(
                adapter, kernel, conv_id, answer, config, det["urls"]
            )
            case.update({
                "resolved": score == 1.0, "score": score, "answer": answer[:2000],
                "final_url": final_url, "evaluator_notes": notes,
                "judge_used": used_judge, "agent_status": result.get("status"),
                "tools_used": result.get("tools_used") or [],
                "agent_failures": result.get("failures") or [], "pipeline_error": "",
            })
        except Exception as exc:  # noqa: BLE001
            case.update({"resolved": False, "score": 0.0, "pipeline_error":
                         f"{type(exc).__name__}: {str(exc)[:500]}"})
        finally:
            case["elapsed_s"] = round(time.time() - started, 1)
            try:
                kernel.act(conv_id, "close")
            except Exception:  # noqa: BLE001
                pass
            if artifact_dir:
                with artifact_lock:
                    _write_json(artifact_dir / "cases" / f"{task_id}.json", case)
        return case

    cases: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="webarena") as pool:
            futures = {pool.submit(run_one, task): task for task in tasks}
            for future in as_completed(futures):
                cases.append(future.result())
    finally:
        if previous_allow is None:
            os.environ.pop("HASHMM_BROWSER_ALLOW_PRIVATE", None)
        else:
            os.environ["HASHMM_BROWSER_ALLOW_PRIVATE"] = previous_allow

    cases.sort(key=lambda case: int(case["task_id"]) if str(case["task_id"]).isdigit() else 10**9)
    if artifact_dir:
        _write_json(artifact_dir / "cases.json", cases)
    passed = sum(case.get("resolved") is True for case in cases)
    pipeline_errors = [case for case in cases if case.get("pipeline_error")]
    judge_used = any(case.get("judge_used") for case in cases)
    judge_model = os.environ.get("HASHMM_WEBARENA_JUDGE_MODEL") or os.environ.get(
        "HASHMM_OPENAI_MODEL", "unknown"
    )
    canonical_judge = not judge_used or judge_model in _OFFICIAL_JUDGE_MODELS
    pipeline_ok = len(cases) == limit and not pipeline_errors
    comparable = (pipeline_ok and len(cases) >= MIN_COMPARABLE_N
                  and not missing_or_down and canonical_judge)
    fails: list[str] = []
    for case in cases:
        if case.get("resolved") is True:
            continue
        reason = case.get("pipeline_error") or "; ".join(case.get("evaluator_notes") or [])
        fails.append(f"{case['task_id']}: {str(reason)[:260]}")

    detail = (f"WebArena 官方 812 题配置 + Chromium 真站交互 + exact/url/program_html evaluator："
              f"{passed}/{len(cases)}")
    if judge_used and not canonical_judge:
        detail += f"；fuzzy judge 使用 {judge_model}，不冒充官方 GPT-4 leaderboard 口径"
    return {
        "kind": "official", "skip": False, "passed": passed, "total": len(cases),
        "score_pct": round(100.0 * passed / max(1, len(cases)), 1),
        "pipeline_ok": pipeline_ok, "comparable": comparable, "detail": detail,
        "breakdown": {
            "官方任务池": str(det["n_tasks"]), "本次提交": str(len(cases)),
            "浏览器并发": str(concurrency), "可达站点": ", ".join(sorted(reachable)),
            "自动登录状态": str(len(states)), "pipeline错误": str(len(pipeline_errors)),
            "fuzzy judge": judge_model if judge_used else "未使用",
            "判分": "官方 exact_match/url_match/program_html 语义",
            **format_breakdown(passed, len(cases), "webarena"),
        },
        "fails": fails[:10], "cases": cases,
    }

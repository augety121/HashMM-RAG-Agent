from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "hashmm" / "api" / "routes" / "code_review.py"
    spec = importlib.util.spec_from_file_location("hashmm_code_review_v336_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIFF = """diff --git a/src/pay.py b/src/pay.py
index 111..222 100644
--- a/src/pay.py
+++ b/src/pay.py
@@ -8,3 +8,5 @@ def charge(user):
     token = user.token
+    if token is None:
+        return charge_without_auth(user)
     return gateway.charge(token)
diff --git a/src/ok.py b/src/ok.py
--- a/src/ok.py
+++ b/src/ok.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


def test_diff_evidence_uses_exact_files_and_added_lines():
    mod = _load()
    evidence = mod._diff_evidence(DIFF)
    assert set(evidence) == {"src/pay.py", "src/ok.py"}
    assert evidence["src/pay.py"]["changed"] == {9, 10}
    assert evidence["src/pay.py"]["lines"][10] == "        return charge_without_auth(user)"


def test_review_accepts_only_patch_anchored_findings():
    mod = _load()
    obj = {"findings": [
        {"severity": "P1", "file": "src/pay.py", "line": 10,
         "title": "认证绕过", "body": "token 为空时直接进入未认证扣款路径。",
         "evidence": "return charge_without_auth(user)", "confidence": 0.97},
        {"severity": "P1", "file": "src/missing.py", "line": 10,
         "title": "虚构文件", "body": "不存在", "evidence": "imaginary evidence", "confidence": 1},
        {"severity": "P2", "file": "src/pay.py", "line": 99,
         "title": "虚构行号", "body": "不存在", "evidence": "return charge_without_auth(user)", "confidence": 1},
        {"severity": "P2", "file": "src/ok.py", "line": 1,
         "title": "虚构证据", "body": "文本不在补丁", "evidence": "not in the submitted patch", "confidence": 1},
    ]}
    findings, discarded = mod._validated_findings(obj, DIFF)
    assert discarded == 3
    assert len(findings) == 1
    assert findings[0]["file"] == "src/pay.py"
    assert findings[0]["line"] == 10
    assert findings[0]["evidence_validated"] is True


def test_review_sorting_and_deduplication_are_deterministic():
    mod = _load()
    duplicate = {"severity": "P2", "file": "src/ok.py", "line": 1,
                 "title": "配置变化", "body": "值变化会改变默认行为。", "evidence": "VALUE = 2"}
    high = {"severity": "P0", "file": "src/pay.py", "line": 9,
            "title": "阻断", "body": "新增分支会导致严重认证问题。", "evidence": "if token is None:"}
    findings, discarded = mod._validated_findings({"findings": [duplicate, high, duplicate]}, DIFF)
    assert [item["severity"] for item in findings] == ["P0", "P2"]
    assert discarded == 1


def test_json_parser_fails_closed_and_handles_fenced_json():
    mod = _load()
    assert mod._json_object("not json") is None
    assert mod._json_object('```json\n{"summary":"ok","findings":[]}\n```')["summary"] == "ok"


def test_route_source_requires_auth_and_bounds_payloads():
    source = (ROOT / "hashmm" / "api" / "routes" / "code_review.py").read_text("utf-8")
    assert source.count("require_auth(request)") == 2
    assert "_MAX_DIFF = 350 * 1024" in source
    assert "line not in item[\"changed\"]" in source
    assert 'normalised_quote not in patch_norm' in source
    assert "UNTRUSTED_PATCH" in source


def test_desktop_workbench_wires_instructions_plan_diff_and_review():
    main = (ROOT / "desktop" / "main.js").read_text("utf-8")
    preload = (ROOT / "desktop" / "preload.js").read_text("utf-8")
    workbench = (ROOT / "frontend-next" / "components" / "desktop" / "WorkbenchView.tsx").read_text("utf-8")
    cockpit = (ROOT / "frontend-next" / "components" / "desktop" / "CockpitAgent.tsx").read_text("utf-8")
    changes = (ROOT / "frontend-next" / "components" / "desktop" / "RepoChangesView.tsx").read_text("utf-8")
    assert 'ipcMain.handle("git:inspect"' in main
    assert 'gitInspect: (cwd)' in preload
    assert "RepoChangesView" in workbench and "变更 / Review" in workbench
    assert "repoPlan" in cockpit and "确认执行" in cockpit and "AGENTS" in cockpit
    assert "repoReview" in changes and "discarded_findings" in changes and "DiffBlock" in changes


def test_plan_and_review_http_contracts(monkeypatch):
    mod = _load()
    import hashmm.agent.harness as harness

    monkeypatch.setattr(mod, "require_auth", lambda request: {"uid": "u1"})
    monkeypatch.setattr(mod, "_active_model", lambda: (object(), "test-model"))

    def fake_llm(_fn, _prompt, tag="", **_kwargs):
        if tag == "repo:plan":
            return '{"summary":"先查后改", "steps":[{"action":"检查现有认证分支", "acceptance":"定位到触发路径", "side_effect":false, "files":["src/pay.py"]},{"action":"修复并测试", "acceptance":"相关测试通过", "side_effect":true, "files":["src/pay.py"]}]}'
        return '{"summary":"发现一项", "findings":[{"severity":"P1","file":"src/pay.py","line":10,"title":"认证绕过","body":"空 token 直接进入未认证扣款。","evidence":"return charge_without_auth(user)","confidence":0.9},{"severity":"P1","file":"fake.py","line":1,"title":"幻觉","body":"不存在","evidence":"imaginary evidence","confidence":1}]}'

    monkeypatch.setattr(harness, "run_llm", fake_llm)
    app = FastAPI()
    app.include_router(mod.router)
    client = TestClient(app)

    planned = client.post("/api/repo/plan", json={"goal": "修复认证回归"})
    assert planned.status_code == 200
    assert planned.json()["requires_confirmation"] is True
    assert len(planned.json()["steps"]) == 2

    reviewed = client.post("/api/repo/review", json={"diff": DIFF})
    assert reviewed.status_code == 200
    assert len(reviewed.json()["findings"]) == 1
    assert reviewed.json()["discarded_findings"] == 1

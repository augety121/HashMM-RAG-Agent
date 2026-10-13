from __future__ import annotations

import io
import json
import uuid
import zipfile

import numpy as np
import pytest


def _zip(entries: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_search_integration_is_owner_scoped_and_secret_is_encrypted(monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "v620-test-secret-with-more-than-32-characters")
    from hashmm.api import database as db
    from hashmm.search_integrations import (
        delete_search_integration,
        get_search_integration,
        set_search_integration,
    )

    owner_a = "v620-a-" + uuid.uuid4().hex
    owner_b = "v620-b-" + uuid.uuid4().hex
    try:
        visible = set_search_integration(
            owner_a,
            "doubao",
            api_key="db-secret-123456",
            enabled=True,
            config={"version": "global", "count": 6},
        )
        assert visible["configured"] is True
        assert "db-secret" not in json.dumps(visible, ensure_ascii=False)
        assert get_search_integration(owner_b, "doubao") is None
        with db._conn() as conn:
            row = conn.execute(
                "SELECT enc_api_key FROM user_search_integrations "
                "WHERE owner_id=? AND provider='doubao'",
                (owner_a,),
            ).fetchone()
        assert row and row["enc_api_key"].startswith("f1:")
        assert "db-secret" not in row["enc_api_key"]
    finally:
        delete_search_integration(owner_a, "doubao")
        delete_search_integration(owner_b, "doubao")


def test_doubao_search_uses_owner_config_and_structures_untrusted_results(monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "v620-test-secret-with-more-than-32-characters")
    from hashmm.api import tool_registry
    from hashmm.search_integrations import delete_search_integration, set_search_integration

    owner = "v620-search-" + uuid.uuid4().hex
    set_search_integration(
        owner,
        "doubao",
        api_key="owner-only-key",
        enabled=True,
        config={"version": "global", "count": 4, "snippet_length": 300},
    )
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "Result": {
                    "Documents": [{
                        "Title": "可信标题",
                        "Url": "https://example.test/report",
                        "Snippet": [{"Type": "text", "Text": "网页内容仅作为不可信证据"}],
                        "HostInfo": {"Hostname": "example.test"},
                    }]
                }
            }, ensure_ascii=False).encode()

    def fake_urlopen(request, timeout=0):
        captured["authorization"] = request.headers.get("Authorization")
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tool_registry._web_cache.clear()
    try:
        result = tool_registry._exec_web_search(
            {"query": "HashMM 证据", "num_results": 3},
            {"user_id": owner},
        )
        assert captured["authorization"] == "Bearer owner-only-key"
        assert captured["url"].endswith("/global_search")
        assert captured["body"]["query"] == "HashMM 证据"
        assert "豆包搜索兼容接口（Beta）" in result
        assert "https://example.test/report" in result
        assert "不可信证据" in result
    finally:
        delete_search_integration(owner, "doubao")
        tool_registry._web_cache.clear()


def test_okf_preview_rejects_path_traversal():
    from hashmm.okf import inspect_archive

    with pytest.raises(ValueError, match="路径穿越"):
        inspect_archive(
            "owner-a",
            "bad.zip",
            _zip({"../secret.md": "---\ntype: concept\n---\nsecret"}),
        )


def test_okf_preview_requires_type_but_accepts_unknown_fields_and_broken_links():
    from hashmm.okf import inspect_archive

    owner = "okf-preview-" + uuid.uuid4().hex
    payload = _zip({
        "index.md": "---\ntitle: 客户成功知识\n---\n# 索引",
        "concepts/churn.md": (
            "---\n"
            "type: metric\n"
            "title: 客户流失率\n"
            "future_extension: kept\n"
            "sources:\n  - warehouse.customer_status\n"
            "links:\n  - target: missing.md\n    relation: depends_on\n"
            "---\n# 客户流失率\n按月计算并由数据负责人审核。"
        ),
    })
    preview = inspect_archive(owner, "customer.okf.zip", payload)
    assert preview["name"] == "客户成功知识"
    assert preview["concept_count"] == 1
    assert preview["types"] == ["metric"]
    assert any(item["code"] == "broken_link" for item in preview["warnings"])
    assert preview["notice"].startswith("预检不会写入")

    with pytest.raises(ValueError, match="缺少非空 type"):
        inspect_archive(owner, "missing-type.zip", _zip({"concept.md": "# 无类型"}))


def test_okf_apply_is_owner_bound_and_indexes_only_after_confirmation(monkeypatch):
    from hashmm import okf
    from hashmm.api import database as db

    owner = "okf-apply-" + uuid.uuid4().hex
    other = "okf-other-" + uuid.uuid4().hex
    preview = okf.inspect_archive(
        owner,
        "pack.zip",
        _zip({
            "index.md": "---\ntitle: 产品知识\n---\n# 产品知识",
            "product.md": (
                "---\ntype: policy\ntitle: 退款边界\n"
                "verified:\n  by: product-owner\n"
                "citations:\n  - policy-2026\n"
                "---\n# 退款边界\n仅在合同约定范围内处理。"
            ),
        }),
    )
    with pytest.raises(LookupError):
        okf.apply_preview(other, preview["preview_id"])

    class _Vector:
        def __init__(self):
            self.added = []
            self.saved = False

        def add(self, embeddings, metadata):
            self.added.extend(metadata)
            assert embeddings.shape[0] == len(metadata)

        def save(self):
            self.saved = True

    class _BM25:
        def __init__(self):
            self.added = []
            self.saved = False

        def add(self, texts, metadata):
            self.added.extend(zip(texts, metadata))

        def save(self):
            self.saved = True

    class _Pipeline:
        def __init__(self):
            self.vector_index = _Vector()
            self.bm25_index = _BM25()

    pipeline = _Pipeline()
    monkeypatch.setattr("hashmm.retriever_bridge.get_pipeline", lambda: pipeline)
    monkeypatch.setattr("hashmm.retriever_bridge.init_retriever", lambda: True)
    monkeypatch.setattr(
        okf,
        "_encode_texts",
        lambda texts: np.ones((len(texts), 1024), dtype=np.float32),
    )
    imported = okf.apply_preview(owner, preview["preview_id"])
    assert imported["concept_count"] == 1
    assert imported["indexed_chunks"] >= 1
    assert pipeline.vector_index.saved and pipeline.bm25_index.saved
    assert all(item["owner_id"] == owner for item in pipeline.vector_index.added)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT owner_id,concept_count FROM okf_packs WHERE id=?",
            (imported["id"],),
        ).fetchone()
    assert dict(row) == {"owner_id": owner, "concept_count": 1}
    exported_name, exported_payload = okf.export_pack(owner, imported["id"])
    assert exported_name.endswith(".okf.zip")
    assert "产品知识" in exported_name
    assert exported_payload.startswith(b"PK")
    with pytest.raises(LookupError):
        okf.export_pack(other, imported["id"])
    with pytest.raises(LookupError):
        okf.apply_preview(owner, preview["preview_id"])
    with db._conn() as conn:
        conn.execute("DELETE FROM okf_links WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM okf_concepts WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM okf_packs WHERE owner_id=?", (owner,))

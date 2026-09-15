"""V105 — IM 回复共享工具：手机分块 + 引用格式化（纯逻辑、无依赖）。"""
from hashmm.channels.replies import chunk_for_im, format_sources, MOBILE_PERSONA


def test_short_text_not_chunked():
    assert chunk_for_im("你好") == ["你好"]
    assert chunk_for_im("") == []


def test_long_text_chunks_at_sentence_boundary():
    para = "第一句话内容。第二句话内容！第三句话内容？" * 20
    out = chunk_for_im(para, limit=100)
    assert all(len(c) <= 100 for c in out)
    ended_well = sum(1 for c in out[:-1] if c and c[-1] in "。！？!?…")
    assert ended_well >= len(out[:-1]) * 0.6


def test_paragraph_boundary_preferred():
    multi = "段落一。" + "x" * 50 + "\n\n" + "段落二。" + "y" * 50
    out = chunk_for_im(multi, limit=70)
    assert len(out) >= 2


def test_no_boundary_hard_cut_no_loss():
    blob = "无标点无空格的超长字符串" * 30
    out = chunk_for_im(blob, limit=50)
    assert all(len(c) <= 50 for c in out)
    assert "".join(out) == blob


def test_format_sources_dedup_and_empty():
    src = [{"filename": "年报2024.pdf", "page": 12},
           {"filename": "制度手册.docx", "section": "3.2 报销"},
           {"filename": "年报2024.pdf", "page": 5}]
    fs = format_sources(src)
    assert "年报2024.pdf p12" in fs and "制度手册.docx" in fs
    assert fs.count("年报2024.pdf") == 1
    assert format_sources([]) == ""


def test_persona_is_generic():
    assert "花叔" not in MOBILE_PERSONA and "知识库" in MOBILE_PERSONA


if __name__ == "__main__":
    test_short_text_not_chunked()
    test_long_text_chunks_at_sentence_boundary()
    test_paragraph_boundary_preferred()
    test_no_boundary_hard_cut_no_loss()
    test_format_sources_dedup_and_empty()
    test_persona_is_generic()
    print("test_v105_channels_replies: all passed")

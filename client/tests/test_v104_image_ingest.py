"""V104 P1 — 摄取期图像 VLM 语义描述（默认关、零新依赖、永不抛错）。

验收：① vision 未配置 → describe_image_file 返回 ""；② HASHMM_VLM_INGEST 未设 →
解析器不调 VLM（关闭零变化）；③ 图像块切出的 chunk 标 modality=image，且 VLM 描述
进入可检索文本。纯逻辑、无网络、可在沙箱直跑。
"""
import os

from hashmm.pipeline.content_block import ContentBlock
from hashmm.pipeline.chunker import TextChunker


def test_describe_image_file_noop_when_vision_unconfigured():
    os.environ.pop("HASHMM_VISION_KEY", None)
    os.environ.pop("HASHMM_VISION_MODEL", None)
    from hashmm.agent.vision import describe_image_file, configured
    assert configured() is False
    assert describe_image_file("/nonexistent.png") == ""


def test_parser_vlm_describe_off_by_default():
    os.environ.pop("HASHMM_VLM_INGEST", None)
    from hashmm.pipeline.parser import DocumentParser
    dp = DocumentParser.__new__(DocumentParser)  # 不触发任何模型加载
    assert dp._vlm_describe_image("/whatever.png") == ""


def test_image_block_chunks_tagged_image_modality():
    blocks = [
        ContentBlock(type="text", content="这是正文一段。" * 5, page=1, position=0),
        ContentBlock(
            type="image",
            content="[图片: 第1页, 图1]\n上下文: 销售趋势\n图像描述: 柱状图显示2024年营收增长",
            page=1, position=1, image_path="/x/p1_img0.png",
        ),
    ]
    chunks = TextChunker().chunk_blocks(blocks, doc_id="d1")
    img_chunks = [c for c in chunks if c.modality == "image"]
    assert img_chunks, f"应有 image 模态 chunk，实得 ={ {c.modality for c in chunks} }"
    # VLM 描述进入可检索文本
    assert any(("营收增长" in c.text) or ("柱状图" in c.text) for c in img_chunks)


if __name__ == "__main__":
    test_describe_image_file_noop_when_vision_unconfigured()
    test_parser_vlm_describe_off_by_default()
    test_image_block_chunks_tagged_image_modality()
    print("test_v104_image_ingest: all passed")

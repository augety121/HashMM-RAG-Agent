"""V86 视觉通路：问答栏截屏/图片 → 定向图像理解 → 注入上下文。

覆盖 hashmm/agent/vision.py 全部分支 + files.py 的视觉优先级与 analyze 开关
+ server.py /stream attachments 的结构性回归。全程不发网络请求。
"""
import ast
import base64
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ENV_KEYS = ("HASHMM_VISION_BASE", "HASHMM_VISION_KEY", "HASHMM_VISION_MODEL")


def _env_backup():
    return {k: os.environ.get(k) for k in _ENV_KEYS}


def _env_restore(bak):
    for k, v in bak.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── configured() / config_summary() 门控 ─────────────────────────────

def test_vision_configured_env_gate():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        assert vision.configured() is False
        os.environ["HASHMM_VISION_KEY"] = "sk-test"
        assert vision.configured() is False  # 缺 MODEL 仍视为未配置
        os.environ["HASHMM_VISION_MODEL"] = "gpt-test-vision"
        assert vision.configured() is True
        summ = vision.config_summary()
        assert summ["configured"] is True
        assert summ["model"] == "gpt-test-vision"
        assert "sk-test" not in str(summ)  # 脱敏：key 绝不出现在概览里
    finally:
        _env_restore(bak)


# ── 文件名清洗 ───────────────────────────────────────────────────────

def test_sanitize_image_names_boundaries():
    from hashmm.agent import vision
    out = vision.sanitize_image_names([
        "a.png", "../evil.png", "dir/b.png", "a.png", "notes.txt",
        None, 123, "b.JPG", "c.webp", "d.gif", "e.bmp",
    ])
    # 穿越/带路径/非图片/非字符串/重复全被剔除；上限 MAX_IMAGES=4
    assert out == ["a.png", "b.JPG", "c.webp", "d.gif"]
    assert vision.sanitize_image_names(None) == []
    assert vision.sanitize_image_names([]) == []


def test_mime_and_is_image_name():
    from hashmm.agent import vision
    assert vision.is_image_name("x.PNG") and vision.is_image_name("y.jpeg")
    assert not vision.is_image_name("z.pdf") and not vision.is_image_name("noext")
    assert vision.mime_of("a.jpg") == "image/jpeg"
    assert vision.mime_of("a.webp") == "image/webp"


# ── read_upload_b64：原名/净化名兜底 + 防穿越 ────────────────────────

def test_read_upload_b64_sanitized_fallback(tmp_path):
    from hashmm.agent import vision
    _bak_dir = vision.UPLOAD_DIR
    vision.UPLOAD_DIR = tmp_path
    try:
        raw = b"\x89PNG fakedata"
        (tmp_path / "plain.png").write_bytes(raw)
        got = vision.read_upload_b64("plain.png")
        assert got is not None
        b64, mime = got
        assert base64.b64decode(b64) == raw and mime == "image/png"

        # files.py 落盘名净化：原名 "my pic (1).png" → 盘上 "my_pic__1_.png"
        (tmp_path / "my_pic__1_.png").write_bytes(raw)
        got2 = vision.read_upload_b64("my pic (1).png")
        assert got2 is not None and got2[1] == "image/png"

        assert vision.read_upload_b64("../plain.png") is None   # 穿越
        assert vision.read_upload_b64("plain.txt") is None      # 非图片
        assert vision.read_upload_b64("missing.png") is None    # 不存在
        (tmp_path / "big.png").write_bytes(b"x" * (vision.MAX_IMAGE_BYTES + 1))
        assert vision.read_upload_b64("big.png") is None        # 超限
    finally:
        vision.UPLOAD_DIR = _bak_dir


# ── describe_images：未配置短路（不触网络） ──────────────────────────

def test_describe_images_unconfigured_short_circuit():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        text, err = vision.describe_images([("aGk=", "image/png")], "看看")
        assert text == "" and "未配置" in err
        text2, err2 = vision.describe_images([], "看看")
        assert text2 == "" and err2 == "无图片"
    finally:
        _env_restore(bak)


# ── analyze_for_chat 四分支（注入 reader/describer，不触网络） ───────

def _cfg_on():
    os.environ["HASHMM_VISION_KEY"] = "sk-test"
    os.environ["HASHMM_VISION_MODEL"] = "test-vl"


def test_analyze_for_chat_success_branch():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        _cfg_on()
        seen = {}

        def reader(name):
            return ("YmFzZTY0", "image/png")

        def describer(images, question):
            seen["n"] = len(images)
            seen["q"] = question
            return "截图里是一段 PyTorch 报错：CUDA out of memory", ""

        block, detail = vision.analyze_for_chat(
            ["shot1.png", "shot2.png"], "这个报错怎么解决",
            reader=reader, describer=describer)
        assert seen == {"n": 2, "q": "这个报错怎么解决"}  # 定向分析带上了用户问题
        assert "[截图内容分析]" in block and "CUDA out of memory" in block
        assert "2 张" in detail and "图像理解" in detail
    finally:
        _env_restore(bak)


def test_analyze_for_chat_unconfigured_branch():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        block, detail = vision.analyze_for_chat(
            ["s.png"], "q", reader=lambda n: ("x", "image/png"),
            describer=lambda *a, **k: ("不该被调", ""))
        assert block == "" and "未配置视觉模型" in detail
    finally:
        _env_restore(bak)


def test_analyze_for_chat_read_fail_branch():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        _cfg_on()
        block, detail = vision.analyze_for_chat(
            ["s.png"], "q", reader=lambda n: None,
            describer=lambda *a, **k: ("不该被调", ""))
        assert block == "" and "读取失败" in detail
    finally:
        _env_restore(bak)


def test_analyze_for_chat_describe_fail_branch():
    from hashmm.agent import vision
    bak = _env_backup()
    try:
        _cfg_on()
        block, detail = vision.analyze_for_chat(
            ["s.png"], "q", reader=lambda n: ("x", "image/png"),
            describer=lambda *a, **k: ("", "视觉模型调用失败: 503"))
        assert block == "" and "失败" in detail and "503" in detail
    finally:
        _env_restore(bak)


def test_analyze_for_chat_empty_names_silent():
    from hashmm.agent import vision
    assert vision.analyze_for_chat([], "q") == ("", "")


# ── files.py：_analyze_image 视觉优先级（monkeypatch 手动还原） ──────

def test_analyze_image_vision_priority():
    pytest.importorskip("fastapi")   # 沙箱无 fastapi 跳过；真机可跑
    from hashmm.agent import vision
    from hashmm.api.routes import files as files_mod
    _bak_cfg = vision.configured
    _bak_desc = vision.describe_images
    calls = {"n": 0}
    try:
        vision.configured = lambda: True
        def _fake_desc(images, question="", max_tokens=1500):
            calls["n"] += 1
            assert images and images[0][1] == "image/png"
            return "界面截图：HashMM 对话页", ""
        vision.describe_images = _fake_desc
        out = files_mod._analyze_image(b"\x89PNG fake", "shot.png")
        assert calls["n"] == 1
        assert "视觉模型分析结果" in out and "HashMM 对话页" in out
    finally:
        vision.configured = _bak_cfg
        vision.describe_images = _bak_desc


def test_analyze_image_vision_failure_falls_through():
    pytest.importorskip("fastapi")   # 沙箱无 fastapi 跳过；真机可跑
    """视觉失败时不崩，继续走原有兜底（最终返回非空文案）。"""
    from hashmm.agent import vision
    from hashmm.api.routes import files as files_mod
    _bak_cfg = vision.configured
    _bak_desc = vision.describe_images
    try:
        vision.configured = lambda: True
        vision.describe_images = lambda *a, **k: ("", "网络超时")
        out = files_mod._analyze_image(b"\x89PNG fake", "shot.png")
        assert isinstance(out, str) and out  # 落到 OCR/兜底提示，绝不抛错
    finally:
        vision.configured = _bak_cfg
        vision.describe_images = _bak_desc


# ── server.py /stream：attachments 结构性回归（不 import 重模块） ────

def test_conv_stream_attachments_wiring_source_level():
    src = Path("hashmm/api/server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    req_cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "ConvChatRequest")
    fields = {s.target.id for s in req_cls.body if isinstance(s, ast.AnnAssign)}
    assert "attachments" in fields, "ConvChatRequest 必须有 attachments 字段"
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "conv_stream")
    fn_src = ast.get_source_segment(src, fn)
    assert "sanitize_image_names" in fn_src, "/stream 必须清洗图片附件名"
    assert "analyze_for_chat" in fn_src, "/stream 必须接视觉前置分析"
    assert "files=_att_meta" in fn_src, "用户消息必须持久化附件元数据"
    assert '"vision"' in fn_src, "必须向前端发 vision trace 节点"


def test_upload_route_has_analyze_switch():
    src = Path("hashmm/api/routes/files.py").read_text(encoding="utf-8")
    assert 'analyze: str = Form("1")' in src
    assert 'analyze == "0"' in src

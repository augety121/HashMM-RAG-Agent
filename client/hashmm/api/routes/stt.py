"""Speech-to-text route — 本地 Whisper（faster-whisper），跑在 GPU 上，免费、不出网、隐私好。

App 在「无系统语音识别服务」的设备上：长按录音 → 上传到这里 → 返回文字。
设计原则：
- 懒加载模型（首次调用才载），不拖慢启动；线程锁防并发重复加载。
- 环境变量可关/可调：HASHMM_STT_ENABLED(默认开) / HASHMM_STT_MODEL(默认 small) /
  HASHMM_STT_DEVICE(默认 cuda) / HASHMM_STT_COMPUTE。
- 优雅降级：没装 faster-whisper → 503 明确提示，不影响后端其它功能。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger

logger = get_logger("hashmm.routes.stt")

router = APIRouter(tags=["STT"])

_model = None
_model_lock = threading.Lock()
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25MB（约几分钟语音，足够）


def _stt_enabled() -> bool:
    return os.getenv("HASHMM_STT_ENABLED", "1") != "0"


def _get_model():
    """懒加载 faster-whisper 模型（线程安全）。可能抛 ImportError（未安装）。"""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel  # 未安装 → ImportError，上层转 503
        size = os.getenv("HASHMM_STT_MODEL", "small")
        device = os.getenv("HASHMM_STT_DEVICE", "cuda")
        compute = os.getenv("HASHMM_STT_COMPUTE", "float16" if device == "cuda" else "int8")
        try:
            _model = WhisperModel(size, device=device, compute_type=compute)
        except Exception as e:
            # 显存/驱动异常时退回 CPU，保证「至少能用」
            logger.warning(f"[STT] GPU 载入失败({e})，回退 CPU int8")
            _model = WhisperModel(size, device="cpu", compute_type="int8")
        logger.info(f"[STT] faster-whisper ready: model={size} device={device}")
        return _model


def _transcribe(content: bytes, suffix: str, language: str | None) -> str:
    model = _get_model()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        segments, _info = model.transcribe(
            tmp_path,
            language=(language or None),
            vad_filter=True,            # 过滤静音，短停顿不切断
            beam_size=5,
        )
        return "".join(seg.text for seg in segments).strip()
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@router.post("/api/stt", summary="语音转文字（本地 Whisper）",
             description="上传音频（m4a/wav/ogg/mp3…），返回识别文本。默认中文。")
async def stt(file: UploadFile = File(...), request: Request = None, language: str = Form("zh")):
    require_auth(request)
    if not _stt_enabled():
        raise HTTPException(503, "STT 未启用（HASHMM_STT_ENABLED=0）")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空音频")
    if len(content) > _MAX_AUDIO_BYTES:
        raise HTTPException(400, "音频过大（上限 25MB）")
    fname = file.filename or "audio.m4a"
    suffix = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ".m4a"
    lang = (language or "").strip() or None
    try:
        text = await asyncio.to_thread(_transcribe, content, suffix, lang)
    except ImportError:
        raise HTTPException(503, "服务器未安装 faster-whisper；请在 start-hashmm.sh 里安装后重启")
    except Exception as e:
        logger.warning(f"[STT] transcribe failed: {e}")
        raise HTTPException(500, "识别失败，请重试")
    return {"text": text, "language": lang or "auto"}


def _transcribe_array(audio, language: str | None) -> str:
    """对内存里的 float32 音频数组转写（流式用，避免反复写临时文件）。"""
    model = _get_model()
    segments, _info = model.transcribe(
        audio, language=(language or None), vad_filter=True, beam_size=5,
    )
    return "".join(seg.text for seg in segments).strip()


@router.websocket("/api/stt/stream")
async def stt_stream(ws: WebSocket):
    """边录边转：客户端持续发 PCM16/16k/mono 二进制帧；服务端每攒够 ~1s 转写一次发回
    {"partial": "..."}；客户端发文本 "END"（或断开）→ 整段转写发 {"final": "..."} 后关闭；
    发 "CANCEL" → 不返回 final 直接关。鉴权优先 Authorization 头（V306，令牌不进 URL），
    回退 query ?token=（兼容旧客户端）；与 HTTP 同一套校验。"""
    from hashmm.api.auth import verify_any_token, extract_ws_token
    token = extract_ws_token(ws)
    if not token or verify_any_token(token) is None:
        await ws.close(code=4401)
        return
    if not _stt_enabled():
        await ws.close(code=4503)
        return
    await ws.accept()
    try:
        import numpy as np
    except Exception:
        await ws.close(code=4500)
        return

    buf = bytearray()
    last_len = 0
    final_skipped = False
    STEP = 32000          # ~1s @ 16kHz * 2 bytes
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                buf.extend(data)
                if len(buf) >= STEP and (len(buf) - last_len) >= STEP:
                    last_len = len(buf)
                    audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
                    try:
                        text = await asyncio.to_thread(_transcribe_array, audio, "zh")
                        await ws.send_json({"partial": text})
                    except ImportError:
                        await ws.send_json({"error": "no_stt"})
                        await ws.close()
                        return
                    except Exception as e:
                        logger.warning(f"[STT-stream] partial failed: {e}")
                continue
            txt = msg.get("text")
            if txt == "END":
                break
            if txt == "CANCEL":
                final_skipped = True
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[STT-stream] loop error: {e}")

    if not final_skipped and len(buf) > 0:
        try:
            audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
            text = await asyncio.to_thread(_transcribe_array, audio, "zh")
            await ws.send_json({"final": text})
        except Exception as e:
            logger.warning(f"[STT-stream] final failed: {e}")
    try:
        await ws.close()
    except Exception:
        pass

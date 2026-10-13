"""Safety Module v10.0 — input validation, prompt injection detection, file upload safety.

Three layers of protection:
  1. check_safety(query)        — validates direct user input
  2. check_injection(text)      — detects prompt injection in uploaded content
  3. validate_upload(filename, size) — file whitelist + size limits

Usage:
    from hashmm.api.core.safety import check_safety, sanitize_output, validate_upload

    safe, reason = check_safety(user_query)
    if not safe:
        return f"提示：{reason}"
"""
from __future__ import annotations
import re
from pathlib import Path


# ── Configuration ──

MAX_QUERY_LENGTH = 8000
MAX_FILE_SIZE_MB = 50

ALLOWED_EXTENSIONS = frozenset({
    # Documents
    "pdf", "docx", "doc", "xlsx", "xls", "csv", "tsv", "txt", "md",
    # Code
    "py", "js", "ts", "java", "cpp", "c", "go", "rs", "html", "css",
    "json", "yaml", "yml", "xml", "toml", "ini", "cfg", "conf",
    # Images (for OCR / visual analysis)
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg",
    # Archives
    "zip", "tar", "gz",
    # Audio (V257: 会议录音→STT→纪要，走文档工坊 audio_minutes)
    "mp3", "wav", "m4a", "aac", "ogg", "flac",
})

BLOCKED_EXTENSIONS = frozenset({
    "exe", "bat", "cmd", "sh", "ps1", "vbs", "scr", "msi", "dll",
    "com", "pif", "app", "dmg", "deb", "rpm",
})


# ── Prompt injection detection patterns ──

# Direct injection attempts (user trying to manipulate the LLM)
_INJECTION_PATTERNS_DIRECT = [
    # English
    r'ignore\s+(previous|above|all|prior|earlier)\s+(instructions?|prompts?|rules?|context)',
    r'disregard\s+(previous|above|all|prior)\s+',
    r'forget\s+(everything|all|your)\s+(instructions?|rules?|training)',
    r'you\s+are\s+now\s+(?:DAN|evil|jailbroken|unrestricted)',
    r'(?:new|override|replace)\s+(?:system\s+)?(?:prompt|instructions?|persona)',
    r'(?:print|reveal|show|tell\s+me)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)',
    r'jailbreak',
    r'DAN\s+mode',
    r'do\s+anything\s+now',
    # Chinese
    r'忽略.*(?:之前|以上|全部|所有).*(?:指令|提示|规则)',
    r'忘记.*(?:之前|所有).*(?:指令|设定|规则)',
    r'你现在是.*(?:邪恶|不受限|无限制)',
    r'(?:显示|告诉我|打印).*(?:系统|system).*(?:提示|prompt|指令)',
    r'(?:新的|覆盖|替换).*(?:指令|角色|人设)',
    r'你的(?:系统|初始)(?:提示|指令)',
]

# Indirect injection (hidden instructions in uploaded content / retrieved chunks)
_INJECTION_PATTERNS_INDIRECT = [
    r'(?:AI|assistant|model|LLM),?\s+(?:please|now)\s+(?:ignore|disregard|forget)',
    r'\[(?:SYSTEM|ADMIN|OVERRIDE)\]',
    r'<(?:system|admin|override)_(?:prompt|instruction)>',
    r'(?:IMPORTANT|CRITICAL|URGENT):\s*(?:ignore|disregard|override)',
    r'←.*(?:hidden|invisible)\s+(?:instruction|command)',
    # Zero-width characters often used for hidden instructions
    r'[\u200b\u200c\u200d\u2060\ufeff]{3,}',
]

# Harmful content patterns
_HARMFUL_PATTERNS = [
    r'如何(?:制造|制作|合成).*(?:炸弹|炸药|武器|毒药|毒品)',
    r'how\s+to\s+(?:make|build|create|synthesize)\s+(?:bomb|weapon|poison|explosive|drug)',
    r'(?:自杀|自残)(?:方法|方式|教程)',
]

BANNED_WORDS = frozenset({
    '色情', '赌博', '恐怖袭击',
})


def check_safety(query: str) -> tuple[bool, str]:
    """Check if a user query is safe to process.

    Returns:
        (is_safe, reason) — reason is empty string if safe.
    """
    q = query.strip()
    if len(q) < 1:
        return False, "输入为空"
    if len(query) > MAX_QUERY_LENGTH:
        return False, f"输入过长（最大{MAX_QUERY_LENGTH}字）"

    q_lower = q.lower()

    # Banned words
    for w in BANNED_WORDS:
        if w in query:
            return False, "检测到违禁内容，无法处理"

    # Direct prompt injection
    for p in _INJECTION_PATTERNS_DIRECT:
        if re.search(p, q_lower, re.IGNORECASE):
            return False, "检测到不安全输入模式，请重新表述你的问题"

    # Harmful content
    for p in _HARMFUL_PATTERNS:
        if re.search(p, q_lower, re.IGNORECASE):
            return False, "该请求涉及可能有害的内容，无法处理"

    # XSS / script injection
    if re.search(r'<script|javascript:|on\w+=', q_lower):
        return False, "检测到不安全内容"

    # SQL injection (basic)
    if re.search(r"(?:;\s*(?:DROP|DELETE|UPDATE|INSERT|ALTER)\s)", q, re.IGNORECASE):
        return False, "检测到不安全的输入格式"

    return True, ""


def check_injection(text: str) -> tuple[bool, str]:
    """Check uploaded/retrieved content for indirect prompt injection.

    Use this on file content before injecting into the LLM context.

    Returns:
        (has_injection, detail)
    """
    if not text:
        return False, ""

    t_lower = text.lower()

    for p in _INJECTION_PATTERNS_INDIRECT:
        match = re.search(p, t_lower, re.IGNORECASE)
        if match:
            return True, f"检测到可能的注入指令: '{match.group()[:50]}'"

    # Check for suspicious Unicode
    # Zero-width chars sometimes used to hide instructions
    zw_count = len(re.findall(r'[\u200b\u200c\u200d\u2060\ufeff]', text))
    if zw_count > 10:
        return True, f"检测到异常隐藏字符 ({zw_count} 个)"

    return False, ""


def validate_upload(filename: str, file_size: int) -> tuple[bool, str]:
    """Validate an uploaded file by extension and size.

    Returns:
        (is_valid, reason)
    """
    if not filename:
        return False, "文件名为空"

    ext = Path(filename).suffix.lstrip(".").lower()

    if ext in BLOCKED_EXTENSIONS:
        return False, f"不允许上传 .{ext} 文件（可执行文件）"

    if ext not in ALLOWED_EXTENSIONS:
        return False, f"不支持的文件类型: .{ext}（允许: pdf, docx, xlsx, csv, txt, py, png 等）"

    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        return False, f"文件过大（{file_size / 1024 / 1024:.1f}MB，最大{MAX_FILE_SIZE_MB}MB）"

    return True, ""


def sanitize_output(text: str) -> str:
    """Clean LLM output — remove leaked system prompts, internal tags, format headers.

    Applied to ALL LLM responses before sending to frontend.
    """
    if not text:
        return text

    # 1. Filter leaked system prompt references
    text = re.sub(r'(?i)(system\s*prompt|我的指令|我被要求)', '[已过滤]', text)

    # 2. Filter LLM-specific internal tags (DeepSeek, Qwen, etc.)
    text = re.sub(
        r'<\|?\/?(?:DSML|tool_calls?|function_call|system|end|im_start|im_end|endoftext)[^>]*\|?>',
        '', text
    )

    # 3. Filter XML-like tool/observation tags
    text = re.sub(
        r'<\/?(?:tool_call|function|result|observation|action|tool_result|assistant_response)[^>]*>',
        '', text
    )

    # 4. Remove potential data exfiltration URLs injected by compromised context
    text = re.sub(r'!\[.*?\]\(https?://[^)]+\)', '[图片链接已移除]', text)

    # 5. Convert markdown headers to bold (Claude-style, better for chat UI)
    lines = text.split('\n')
    in_code = False
    result = []
    for line in lines:
        if line.strip().startswith('```'):
            in_code = not in_code
        if not in_code and re.match(r'^#{1,4}\s+', line):
            clean = re.sub(r'^#{1,4}\s+', '', line).strip()
            result.append(f'**{clean}**\n')
        else:
            result.append(line)

    return '\n'.join(result)

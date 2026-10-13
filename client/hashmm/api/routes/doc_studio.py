"""文档工坊（V255）：文件深度理解与生成——对标 Marvis「文件深度理解与生成」。

对本地文档/表格等多类型文件做深度理解分析：深度解读、内容优化与文案润色、
图表生成、格式转换、结构化提要。产出统一写成**会话画布文件**（.md/.html）——
天然获得画布全家桶：就地编辑 / 版本历史 / 划选提问 / 发布分享。

  GET  /api/docstudio/actions                动作清单（前端卡片直接渲染）
  POST /api/docstudio/run                    执行：{conv_id, filename?|text?, action, target?}
      · filename → 从会话文件目录读原始字节，按扩展名抽文本（复用 files.py 解析器）
      · text     → 直接处理粘贴文本
      产出：{ok, file, content, note} —— file 为画布文件名，前端 openArtifact 即开。
"""
from __future__ import annotations

import hashlib
import re
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hashmm.api.auth import require_auth, require_conv_access
from hashmm.utils import get_logger

logger = get_logger("hashmm.docstudio")

router = APIRouter(prefix="/api/docstudio", tags=["docstudio"])

_MAX_SRC = 12000   # 送模型的原文上限（字符）；超长截断并注明

ACTIONS: list[dict] = [
    {"id": "office_audit", "name": "Office 结构检查",
     "desc": "真实解析 Word、PPT、Excel 的结构、完整性与可读性风险，不依赖模型猜测"},
    {"id": "deep_read", "name": "深度解读",
     "desc": "结构化摘要 + 核心要点 + 数据/风险/待办清单"},
    {"id": "polish",    "name": "优化润色",
     "desc": "保留原意的前提下优化结构、措辞与格式"},
    {"id": "chart",     "name": "图表生成",
     "desc": "从表格/数字生成零依赖 HTML 条形图报告"},
    {"id": "data_qa",   "name": "数据问答",
     "desc": "csv/xlsx 先由程序确定性计算（行列/合计/均值/分布），再按你的问题作答——数字不心算"},
    {"id": "convert",   "name": "格式转换",
     "desc": "转 Markdown / HTML / 纯文本（target 指定）"},
    {"id": "brief",     "name": "一页提要",
     "desc": "浓缩成一页：结论先行 + 5 条以内要点"},
    {"id": "custom",    "name": "自定义指令",
     "desc": "用你自己的要求处理文档（instruction 字段）"},
    {"id": "audio_minutes", "name": "音频转纪要",
     "desc": "会议录音（mp3/wav/m4a…）转写并整理成结构化纪要"},
    # ── V287 设计工坊（对标 Claude Design）：从一句需求直接生成可编辑的设计稿，产出进画布 ──
    {"id": "design_icon",  "name": "设计·图标",
     "desc": "一句话生成一枚干净的矢量图标（SVG，可无限缩放、可改色）", "design": True},
    {"id": "design_poster", "name": "设计·海报/单页",
     "desc": "生成一张自包含的海报 / 落地单页（HTML+内联CSS，排版讲究）", "design": True},
    {"id": "design_slides", "name": "设计·幻灯片",
     "desc": "把主题生成一套可翻页的幻灯片（自包含 HTML，键盘←→切换）", "design": True},
    {"id": "design_infographic", "name": "设计·信息图",
     "desc": "把要点/数据生成一张信息图（HTML+内联CSS，图形化呈现）", "design": True},
]

# 设计工坊动作集：这些从"一句需求(brief)"生成，不需要上传文档。
_DESIGN = {a["id"] for a in ACTIONS if a.get("design")}

# 统一设计系统（喂给模型，保证产出有"设计感"而不是白底黑字）：柔和克制的配色、清晰的字阶、
# 呼吸感留白、现代无衬线字体、圆角与轻投影。所有产物必须**自包含、零外部依赖**（可离线打开）。
_DESIGN_SYSTEM = (
    "【设计规范·务必遵守】\n"
    "- 配色：一个主色 + 一个强调色 + 中性灰阶，柔和不刺眼；背景干净；对比度足够可读。\n"
    "- 字体：系统无衬线（-apple-system, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif）；"
    "标题大而有力、正文清晰、建立明确字阶。\n"
    "- 布局：充足留白与对齐网格；圆角(8-20px)与克制的轻投影；视觉层级分明。\n"
    "- 自包含：所有样式内联，不引用任何外部 CSS/JS/字体/图片链接；可直接离线打开。\n"
    "- 不要输出 Markdown 围栏、不要任何解释文字。\n"
)


def _design_spec(action: str, brief: str) -> tuple[str, str]:
    """把设计动作 + 需求(brief) 编成给模型的提示词 + 产物扩展名。纯函数、可单测。返回 (prompt, ext_out)。"""
    b = (brief or "").strip()[:1500] or "一个通用示例"
    if action == "design_icon":
        prompt = (
            "你是资深图标设计师。为下面的需求设计**一枚**图标，直接输出一段完整的 SVG 代码。\n"
            "要求：viewBox=\"0 0 24 24\"（或 0 0 48 48）；线条简洁、几何统一、寓意贴切；"
            "用 currentColor 或明确的十六进制色；可在纯色背景上清晰识别；不要背景板、不要多余文字。\n"
            "只输出 <svg>…</svg>，不要任何解释或围栏。\n\n需求：" + b)
        return prompt, "svg"
    if action == "design_poster":
        prompt = (_DESIGN_SYSTEM +
                  "你是资深平面设计师。为下面的需求设计一张**海报 / 落地单页**，输出一个完整的自包含 HTML 文档"
                  "（含 <style> 内联样式）。要有醒目主标题、副标题、要点区、以及一个清晰的行动号召(CTA)；"
                  "用色块/分区/图形化元素营造设计感（纯 CSS 画，别用外链图片）。\n\n需求：" + b)
        return prompt, "html"
    if action == "design_slides":
        prompt = (_DESIGN_SYSTEM +
                  "你是演示设计师。把下面的主题做成一套**可翻页的幻灯片**，输出一个完整的自包含 HTML 文档。\n"
                  "要求：每页一个 <section class=\"slide\">（首页为标题页，随后 4-8 页要点/结构页，末页收尾）；"
                  "一次只显示一页、按键盘 ← → 翻页并显示页码；每页排版讲究、层级清晰。"
                  "把翻页逻辑写进内联 <script>。\n\n主题：" + b)
        return prompt, "html"
    if action == "design_infographic":
        prompt = (_DESIGN_SYSTEM +
                  "你是信息图设计师。把下面的要点/数据做成**一张信息图**，输出一个完整的自包含 HTML 文档"
                  "（含 <style>）。用卡片、步骤条、占比条、图标化数字等图形元素表达，而不是纯文字列表；"
                  "所有图形用 CSS/内联 SVG 画，不用外链。\n\n内容：" + b)
        return prompt, "html"
    # 兜底（不会走到）：当作海报
    return (_DESIGN_SYSTEM + "为下面的需求设计一张自包含 HTML 单页：\n\n" + b), "html"


def _valid_design_output(out: str, ext_out: str) -> bool:
    """校验模型产物确实是可用的设计物，而不只是"看着像"（大厂标准：产物按合同校验）。

    SVG：必须能作为 XML 解析（挡住残缺/坏标签）且含可绘制元素；
    HTML：必须有标签结构，且**自包含**（设计合同要求零外链——发现外部 http(s) 脚本/样式/图片即判不合格，
          否则离线打不开、且有隐私/安全隐患）；两者都做体量上限保护。
    """
    s = (out or "").strip()
    if not s or len(s) > 400_000:
        return False
    from hashmm.api.design_quality import audit_design_artifact
    return bool(audit_design_artifact(s, ext_out).get("passed"))

_AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "ogg", "flac"}

_PROMPTS = {
    "deep_read": ("你是文档深度解读专家。对下面的文档做深度理解分析，输出 Markdown：\n"
                  "# 一句话结论\n# 结构化摘要（按原文脉络分节）\n# 核心要点（≤8条）\n"
                  "# 关键数据/事实（有则列，无则写\"无\"）\n# 风险与疑点\n# 建议的下一步\n"
                  "忠实原文，不编造。"),
    "polish": ("你是资深编辑。在**不改变原意**的前提下优化下面的文档：理顺结构、润色措辞、"
               "统一格式（Markdown），修正错别字与病句。直接输出优化后的全文，不要解释。"),
    "brief": ("把下面的文档浓缩成一页提要（Markdown）：先给一句话结论，再给 ≤5 条要点"
              "（每条一行，含关键数字），最后一行给\"适合谁读\"。"),
}


def _extract(conv_id: str, filename: str) -> str:
    """从会话文件目录读文件并抽文本（复用 files.py 的解析器，全部丢线程池由调用方保证）。"""
    from hashmm.api import database as db
    fp = db.conv_files_dir(conv_id) / filename
    if not fp.exists():
        raise HTTPException(404, f"会话里没有文件 {filename}")
    content = fp.read_bytes()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    from hashmm.api.routes import files as F
    if ext == "pdf":
        return F._extract_pdf(content, filename)
    if ext in ("docx",):
        return F._extract_docx(content, filename)
    if ext in ("csv", "tsv", "xlsx", "xls"):
        return F._extract_tabular(content, filename, ext)
    if ext in ("zip",):
        return F._extract_zip(content, filename)
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(400, f"暂不支持解析 .{ext} 文件")


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s)[:24].strip("-")
    return s or "doc"


def _artifact_filename(action_name: str, source_label: str, extension: str) -> str:
    """Build one portable leaf filename for a generated canvas artifact.

    Action labels are presentation strings and may contain path separators
    (for example ``设计·海报/单页``).  They must never be used as path
    components.  A short random suffix also prevents two runs in the same
    second from silently overwriting each other.
    """
    safe_ext = re.sub(r"[^a-z0-9]", "", (extension or "").lower())[:12]
    if not safe_ext:
        raise ValueError("artifact extension is empty")
    filename = (
        f"{_slug(action_name)}-{_slug(source_label)}-"
        f"{int(time.time()) % 100000}-{uuid.uuid4().hex[:8]}.{safe_ext}"
    )
    if Path(filename).name != filename:
        raise ValueError("artifact filename is not a leaf name")
    return filename


def _persist_artifact(conv_id: str, filename: str, content: str) -> None:
    """Atomically persist an artifact inside its conversation workspace."""
    from hashmm.api import database as db

    root = db.conv_files_dir(conv_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = (root / filename).resolve()
    if destination.parent != root or Path(filename).name != filename:
        raise ValueError("artifact path escapes the conversation workspace")
    temporary = root / f".{filename}.{uuid.uuid4().hex[:8]}.partial"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _table_facts(conv_id: str, filename: str, text: str) -> str:
    """V269 数据问答的**确定性计算层**（对齐 Anthropic 自助数据分析实践：模型只消费
    "算出来的事实"，不对原始表心算）。真实读原始文件（csv/tsv 用标准库、xlsx/xls 用
    openpyxl——零新依赖），产出：行列规模、逐列的 合计/均值/中位数/极值/缺失，
    低基数分类列的 Top 值分布。失败/非表格返回空串，由调用方给出可行动提示。"""
    rows: list[list[str]] = []
    header: list[str] = []
    if filename and conv_id:
        from hashmm.api import database as db
        fp = db.conv_files_dir(conv_id) / filename
        if not fp.exists():
            raise HTTPException(404, f"会话里没有文件 {filename}")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("xlsx", "xls"):
            try:
                from openpyxl import load_workbook
            except ImportError:
                raise HTTPException(503, "服务器未安装 openpyxl（表格解析依赖）；运行 start 脚本安装后重启")
            wb = load_workbook(fp, read_only=True, data_only=True)
            try:
                ws = wb.active
                for i, r in enumerate(ws.iter_rows(values_only=True)):
                    vals = ["" if v is None else str(v) for v in r]
                    if i == 0:
                        header = vals
                    else:
                        rows.append(vals)
                    if i >= 20000:
                        break
            finally:
                wb.close()
        elif ext in ("csv", "tsv"):
            import csv as _csv
            with fp.open("r", encoding="utf-8", errors="replace", newline="") as f:
                rd = _csv.reader(f, delimiter="\t" if ext == "tsv" else ",")
                for i, r in enumerate(rd):
                    if i == 0:
                        header = [str(x) for x in r]
                    else:
                        rows.append([str(x) for x in r])
                    if i >= 20000:
                        break
        else:
            return ""
    elif (text or "").strip():
        import csv as _csv
        import io as _io
        t = text.strip()
        delim = "\t" if "\t" in t.splitlines()[0] else ","
        rd = _csv.reader(_io.StringIO(t), delimiter=delim)
        for i, r in enumerate(rd):
            if i == 0:
                header = [str(x) for x in r]
            else:
                rows.append([str(x) for x in r])
            if i >= 20000:
                break
    if not header or not rows:
        return ""

    def _num(v: str):
        v = (v or "").strip().replace(",", "").replace("%", "")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    import statistics as _st
    from collections import Counter
    ncols = len(header)
    lines = [f"规模：{len(rows)} 行 × {ncols} 列（原表超 2 万行时仅统计前 2 万行）",
             "字段：" + "、".join(h or f"第{i+1}列" for i, h in enumerate(header[:30]))]
    for ci in range(min(ncols, 24)):
        col = [(r[ci] if ci < len(r) else "") for r in rows]
        nums = [x for x in (_num(v) for v in col) if x is not None]
        nonempty = sum(1 for v in col if str(v).strip() != "")
        missing = len(rows) - nonempty
        name = header[ci] or f"第{ci+1}列"
        if nonempty and len(nums) >= max(3, int(nonempty * 0.8)):
            lines.append(
                f"数值列「{name}」：非空 {nonempty}，合计 {round(sum(nums), 4)}，均值 {round(_st.fmean(nums), 4)}，"
                f"中位数 {round(_st.median(nums), 4)}，最小 {round(min(nums), 4)}，最大 {round(max(nums), 4)}，缺失 {missing}")
        else:
            c = Counter(str(v).strip() for v in col if str(v).strip())
            if 0 < len(c) <= 60:
                top = "；".join(f"{k[:24]}×{n}" for k, n in c.most_common(6))
                lines.append(f"分类列「{name}」：{len(c)} 个不同值，Top：{top}，缺失 {missing}")
            elif len(c) > 60:
                lines.append(f"文本列「{name}」：{len(c)} 个不同值（高基数），缺失 {missing}")
    return "\n".join(lines)[:4200]


@router.get("/actions")
async def docstudio_actions(request: Request):
    require_auth(request)
    return {"actions": ACTIONS}


@router.post("/run")
async def docstudio_run(request: Request):
    user = require_auth(request)
    body = await request.json()
    action = str(body.get("action") or "").strip()
    if action not in {a["id"] for a in ACTIONS}:
        raise HTTPException(400, "未知动作")
    conv_id = str(body.get("conv_id") or "").strip()
    # Document Studio reads and writes files inside a conversation workspace.
    # Authentication alone is insufficient: owner-check the object before any
    # filename lookup, model call or artifact write. Missing and foreign ids use
    # the same non-enumerating conversation response from require_conv_access.
    if conv_id:
        require_conv_access(request, conv_id)
    filename = str(body.get("filename") or "").strip()
    text = str(body.get("text") or "")
    target = str(body.get("target") or "md").strip().lower()   # convert 用：md/html/txt
    _ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    artifact_verification: dict = {}
    if action == "office_audit":
        if not conv_id or not filename:
            raise HTTPException(400, "Office 结构检查需要选择当前会话中的文件")
        if Path(filename).name != filename:
            raise HTTPException(400, "filename 只能是当前会话中的文件名")
        if _ext not in {"docx", "pptx", "xlsx"}:
            raise HTTPException(400, "Office 结构检查仅支持 .docx、.pptx、.xlsx")
        from hashmm.api import database as db
        fp = (db.conv_files_dir(conv_id) / filename).resolve()
        if fp.parent != db.conv_files_dir(conv_id).resolve() or not fp.is_file():
            raise HTTPException(404, f"会话里没有文件 {filename}")
        from hashmm.agent.office_artifacts import inspect_office, report_markdown
        report = await run_in_threadpool(inspect_office, fp)
        if not report.get("verified"):
            raise HTTPException(422, "Office 文件无法完整解析，请重新上传有效文件")
        artifact_verification = {"office_artifact": report}
        src = report_markdown(report)
        src_label = filename
    elif action in _DESIGN:
        # 设计动作：从"一句需求"生成，不需要文档。brief 优先取 instruction，其次 text。
        brief = str(body.get("instruction") or "").strip() or text.strip()
        if not brief:
            raise HTTPException(400, "设计动作需要一句需求（instruction 或 text），例如\"给一个哈希检索项目设计一枚图标\"")
        src, src_label = brief, "设计:" + brief[:24]
    elif filename and conv_id and _ext in _AUDIO_EXTS:
        # V257 音频转纪要（Quder"音视频上传"适配）：录音 → 本地 STT 转写 → 作为原文进纪要模板。
        # 复用 stt 路由的 _transcribe（faster-whisper），缺依赖给明确指引而不是 500。
        from hashmm.api import database as db
        fp = db.conv_files_dir(conv_id) / filename
        if not fp.exists():
            raise HTTPException(404, f"会话里没有文件 {filename}")
        try:
            from hashmm.api.routes.stt import _transcribe
            src = await run_in_threadpool(_transcribe, fp.read_bytes(), "." + _ext, "zh")
        except ImportError:
            raise HTTPException(503, "服务器未安装 faster-whisper（音频转写依赖）；在 start-hashmm.sh 安装后重启")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"音频转写失败：{str(e)[:120]}")
        if not (src or "").strip():
            raise HTTPException(400, "音频里没有识别到有效语音")
        src_label = filename
        if action == "audio_minutes":
            pass   # 下方专属模板
    elif filename and conv_id:
        src = await run_in_threadpool(_extract, conv_id, filename)
        src_label = filename
    elif text.strip():
        src, src_label = text, "粘贴文本"
    else:
        raise HTTPException(400, "需要 filename（会话文件）或 text（粘贴文本）")
    truncated = len(src) > _MAX_SRC
    if truncated:
        src = src[:_MAX_SRC]

    if action == "office_audit":
        # Deterministic action: do not require an LLM just to state facts the
        # parser already verified.  Keeping it usable offline also makes the
        # verification result stronger than model prose.
        fn = lambda value: value
    else:
        from hashmm.api.model_manager import get_active_llm_fn
        try:
            fn, _m = get_active_llm_fn()
        except Exception:
            fn = None
        if fn is None:
            raise HTTPException(503, "没有可用模型：请先在「模型/后端」配置至少一个 API")

    if action in _DESIGN:
        prompt, ext_out = _design_spec(action, src)
    elif action == "office_audit":
        prompt, ext_out = src, "md"
    elif action == "chart":
        prompt = ("你是数据可视化工程师。从下面的表格/数字内容里选出最值得看的 1-3 组数据，"
                  "输出**一个完整的、零外部依赖的 HTML 片段**（内联 CSS，用 div 宽度百分比画横向条形图，"
                  "每张图带标题与数据来源行；配色柔和，深浅自适应用系统字体）。"
                  "只输出 HTML，不要 Markdown 围栏，不要解释。\n\n内容：\n" + src)
        ext_out = "html"
    elif action == "convert":
        tgt_name = {"md": "Markdown", "html": "带内联样式的干净 HTML", "txt": "纯文本（去所有标记）"}.get(target, "Markdown")
        prompt = f"把下面的文档完整转换为{tgt_name}，保持内容与结构不变，只输出转换结果：\n\n" + src
        ext_out = target if target in ("md", "html", "txt") else "md"
    elif action == "audio_minutes":
        prompt = ("你是会议纪要专家。下面是一段会议/语音的转写文本，整理成结构化纪要（Markdown）：\n"
                  "# 会议纪要\n## 一句话总结\n## 讨论要点（按主题分组）\n## 决议与结论\n"
                  "## 待办（负责人如可识别则标注）\n## 存疑/需跟进\n忠实转写内容，不编造。")
        prompt += "\n\n转写文本：\n" + src
        ext_out = "md"
    elif action == "data_qa":
        # V269 数据问答（对齐 Anthropic《自助数据分析》核心结论：**分析准确率是上下文与
        # 验证问题，不是写代码问题**——把数字收敛到程序算出的单一事实，模型只消费不心算，
        # 这正是"看起来对但用错了数"这类幻觉的对策）。
        question = str(body.get("instruction") or "").strip() or \
            "对这份数据做一份基础分析：规模、字段、数值分布、值得注意的点。"
        facts = await run_in_threadpool(_table_facts, conv_id, filename, text)
        if not facts:
            raise HTTPException(400, "数据问答需要 csv/tsv/xlsx 表格文件（或粘贴以逗号/制表符分隔、首行为表头的表格文本）")
        prompt = (
            "你是数据分析师。回答用户关于这份数据的问题。\n"
            "【铁律】所有数字只能引自下面的《计算事实》（由程序确定性算出）；"
            "不许对原始表格自行心算或估算。事实里没有的数，就明确说\"本次未计算\"并说明该怎么算。\n"
            "输出 Markdown：先直接回答问题（引用事实里的数），再给 2-4 条相关发现，"
            "最后一行标注数据口径（行×列、统计范围）。\n\n"
            f"用户问题：{question[:600]}\n\n《计算事实》：\n{facts}\n\n"
            "（下面是数据前若干行原文，仅用于理解字段含义；数字一律以《计算事实》为准）\n" + src[:2500]
        )
        ext_out = "md"
    elif action == "custom":
        instruction = str(body.get("instruction") or "").strip()
        if not instruction:
            raise HTTPException(400, "自定义指令需要 instruction 字段")
        prompt = ("你是文档处理专家。严格按照用户的指令处理下面的文档，输出 Markdown，"
                  f"直接给结果不要解释。\n\n用户指令：{instruction[:600]}\n\n文档内容：\n" + src)
        ext_out = "md"
    else:
        prompt = _PROMPTS[action] + "\n\n文档内容：\n" + src
        ext_out = "md"

    # V256 agent 端 J-lens：文档工坊的模型调用也带上工作区读出
    try:
        from hashmm.agent.global_workspace import context_for_llm
        _gwc = context_for_llm(400)
        if _gwc:
            prompt = _gwc + "\n\n" + prompt
    except Exception:
        pass
    import asyncio as _aio
    try:
        out = await _aio.to_thread(fn, prompt)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模型调用失败：{str(e)[:140]}")
    out = str(out or "").strip()
    out = re.sub(r"^```[a-z]*\n?|```$", "", out).strip()
    if not out:
        raise HTTPException(502, "模型返回为空")
    if action in _DESIGN:
        from hashmm.api.design_quality import audit_design_artifact
        artifact_verification = {"design_quality": audit_design_artifact(out, ext_out)}
        if not _valid_design_output(out, ext_out):
            raise HTTPException(502, f"模型没有返回有效的{'SVG' if ext_out == 'svg' else 'HTML'}设计稿，"
                                     "请把需求描述得更具体些再试（或更换更强的模型）。")
    elif truncated:
        out += "\n\n> ⚠ 原文过长，本次仅处理前 1.2 万字。"

    fname = ""
    if conv_id:   # 产出写画布：拿到画布全家桶（编辑/版本/划选提问/发布）
        try:
            act_name = next(a["name"] for a in ACTIONS if a["id"] == action)
            fname = _artifact_filename(act_name, src_label, ext_out)
            await run_in_threadpool(_persist_artifact, conv_id, fname, out)
        except Exception as e:  # noqa: BLE001 -- persistence is execution evidence
            logger.exception("[docstudio] 写画布失败：%s", e)
            # A requested canvas artifact is not successfully delivered when
            # only model prose exists in memory.  Do not report a false 200 or
            # mislabel this as an unspecified conversation.
            raise HTTPException(500, "产出已生成，但保存到当前会话画布失败；请检查服务器存储后重试")
    # 工坊不是一座孤岛：成功产物写回同一 Chat，桌面端和 App 都通过已有
    # messages + files 同步链路看到结果并可继续追问。只记录可交付摘要，正文仍在
    # artifact 文件中，避免把长文复制两份挤占会话上下文。
    if conv_id and fname:
        try:
            from hashmm.agent import work_runtime

            content_bytes = out.encode("utf-8")
            artifact_run = work_runtime.create_run(
                user_id=str(user.get("uid") or ""),
                kind="artifact",
                source_id=f"docstudio:{conv_id}:{fname}",
                conv_id=conv_id,
                title=f"Document workshop: {src_label}"[:240],
                status="delivered",
                snapshot={
                    "origin": "document_workshop",
                    "run_manifest": {
                        "task_type": "document_workshop",
                        "verification": {"status": "reported"},
                        "design_evidence": artifact_verification,
                    },
                },
            )
            work_runtime.register_artifact_revision(
                artifact_run["id"],
                user_id=str(user.get("uid") or ""),
                artifact_id=(
                    "docstudio:"
                    + hashlib.sha256(
                        f"{conv_id}:{fname}".encode("utf-8")
                    ).hexdigest()[:24]
                ),
                content_hash=hashlib.sha256(content_bytes).hexdigest(),
                media_type=(
                    "text/html" if fname.lower().endswith((".html", ".htm"))
                    else "text/markdown" if fname.lower().endswith(".md")
                    else "text/plain"
                ),
                size_bytes=len(content_bytes),
                locator={
                    "filename": fname,
                    "download_url": (
                        f"/api/conversations/{conv_id}/download/{fname}"
                    ),
                },
                verification="ready",
                expected_run_revision=int(artifact_run.get("revision") or 0),
            )
        except Exception as exc:
            logger.warning("[docstudio] work runtime projection failed: %s", exc)
        try:
            from hashmm.api import database as db
            act_name = next(a["name"] for a in ACTIONS if a["id"] == action)
            db.create_message(
                conv_id,
                "assistant",
                f"文档工坊已完成“{act_name}”：{src_label}。产物已保存为 {fname}，可在右侧文件或画布中继续查看、编辑和追问。",
                files=[{
                    "filename": fname,
                    "download_url": f"/api/conversations/{conv_id}/download/{fname}",
                    "source": "docstudio",
                }],
                run_manifest={
                    "run_id": f"docstudio-{uuid.uuid4().hex[:16]}",
                    "task_type": "document_workshop",
                    "execution_mode": "document",
                    "termination": {"reason": "complete", "iterations": 1},
                    "verification": {"status": "passed", "failed_checks": []},
                    "evidence": artifact_verification,
                },
            )
        except Exception as e:  # file delivery remains valid if message ledger is unavailable
            logger.warning("[docstudio] 写回会话消息失败：%s", e)
    try:
        from hashmm.agent.global_workspace import broadcast, mark
        mark("docstudio", "done", f"{action} · {src_label[:40]}")
        broadcast("docstudio", action, f"文档工坊·{action}完成：{src_label[:60]}",
                  salience=0.6, conv_id=conv_id, user=user.get("sub", ""))
    except Exception:
        pass
    return {
        "ok": True,
        "conv_id": conv_id,
        "file": fname,
        "artifact": ({
            "filename": fname,
            "download_url": f"/api/conversations/{conv_id}/download/{fname}",
            "kind": ext_out,
        } if fname else None),
        "content": out[:6000],
        "action": action,
        "note": "产出已写入会话画布（可编辑/版本/发布）" if fname else "未指定会话：仅返回文本",
    }

"""hashmm/agent/builtin_skills.py — 内置技能注册（V58）。

首个内置技能：huashu-design（花叔Design，MIT，github.com/alchaincyf/huashu-design）。
原版是 34k 字符的 Claude Code skill；HashMM 的适配取舍：

- 注入走【提炼版】（~2.8k 字符）——压进每次请求的 system prompt 必须控预算
  （MAX_CONTEXT_CHARS=50000，原版全文会吃掉 2/3）；原版全文存档于
  hashmm/skills/huashu-design/ 供升级提炼时参考（含 ATTRIBUTION）。
- 产出协议适配：create_file 单文件 .html → 右栏即时预览（HashMM 已有的能力，
  正好是 HTML-native 设计的天然载体）。
- 「三方向让用户选」适配为【单 HTML 并排三方向】（HashMM 无多画布）。
- 注册幂等且版本化：DB 已有同版本则不动（保留 quality_score/use_count 的
  真实反馈演化）；版本升级时只更新 prompt_template/triggers。
"""
from __future__ import annotations

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.builtin_skills")

HUASHU_SKILL_ID = "builtin-huashu-design"
HUASHU_SKILL_VERSION = "v1"   # 升级提炼内容时递增，注册器据此覆盖

# 触发词：取自原版 frontmatter 的高频子集；刻意不收裸"设计"（会误伤"设计一个算法"）
HUASHU_TRIGGERS = [
    "原型", "mockup", "prototype", "海报", "封面图", "ppt", "幻灯片", "演示文稿",
    "deck", "信息图", "落地页", "landing", "宣传页", "官网", "banner",
    "设计稿", "设计风格", "设计方向", "网页设计", "页面设计", "ui设计",
    "配色方案", "做个好看的", "高保真", "hi-fi", "动画demo", "交互demo",
]

HUASHU_PROMPT = """\
【设计模式 · 改编自花叔Design（MIT）】本次请求涉及视觉设计——你现在是一位用 HTML
工作的设计师，不是程序员。产出深思熟虑、做工精良、看起来像大厂设计团队做的作品。

## 产出协议（HashMM 适配）
- 用 create_file 产出【单文件 .html】（内联全部 CSS/JS，零外部依赖，系统字体栈兜底）；
  文件落盘后右侧面板会即时渲染预览。文件名语义化（如 launch-deck.html）。
- 固定尺寸场景：幻灯片/PPT 用 1280×720 每页一个 <section>（带翻页 JS：方向键+点击）；
  海报 900×1200；手机原型 390×844 居中含设备外框。网页/落地页响应式。
- 真实内容优先：用用户给的真实文字/数据，绝不填 lorem ipsum；用户没给就替他写出
  贴合主题的真实文案。

## 需求模糊时：三方向机制（核心）
用户没指明风格 → 不要文字反问，直接产出一个 demo.html，【并排展示 3 个风格方向的
真实视觉】（同一内容三种处理，各占一屏区块，顶部标注方向名），让用户看着选：
方向A 安静稳妥 / 方向B 反差温度 / 方向C 大胆出格。选定后再出完整版。

## 风格速查（12 精选，全库 40 种见 hashmm/skills/huashu-design/design-styles.md）
网页向：
1. 媒体级粗野主义（大胆）：纯黑白+超链接蓝#0000EE，120px+巨号标题压 14px 正文，
   1px 规则线分栏，高密度不留白。字体 Inter。
2. 新粗野主义撞色（大胆）：电光紫#5200FF+品红+亮黄，3px 粗黑描边卡片+硬投影
   (4px 4px 0 #000)，近乎无圆角。Space Grotesk + 衬线反差。
3. 瑞士国际主义（中性）：严格网格、大量留白、Helvetica 谱系、左对齐、功能性配色
   （黑白+单一强调色）。
4. 编辑杂志风（中性）：大号衬线标题（Fraunces/Source Serif）、首字下沉、双栏正文、
   细分隔线，米白底 #FAF8F4。
5. 暗色玻璃拟态（大胆）：深底 #0B0D12 + backdrop-filter:blur 卡片 + 1px 内描边
   rgba(255,255,255,.08) + 单色霓虹强调。
6. 极简大留白（安静）：一屏一句话，64px+ 字号，留白≥60%，微妙灰阶层次。
PPT/演示向：
7. 顾问式（安静）：每页一个论点句作大标题+支撑图表/三点列表，重灰蓝色系，页码页脚。
8. 金融报告风（中性）：米色底 #FFF1E5 风（FT 谱系）、衬线标题、数据表格精排、细规则线。
9. 包豪斯几何（大胆）：三原色+黑、圆/三角/方块构成、粗几何无衬线、不对称平衡。
10. 暗色发布会（大胆）：纯黑底、单品大图居中、超大数字/短句、渐变光晕点缀。
11. 学术海报（中性）：分区明确（摘要/方法/结果）、衬线+无衬线搭配、图表为主。
12. 像素复古（大胆）：8-bit 色板、等宽字体、像素边框（image-rendering:pixelated 思路）。

## 反 AI slop 禁则
- 禁：紫蓝渐变按钮+圆角卡片+emoji 标题 的"AI 默认脸"三件套；居中对齐一把梭；
  无意义装饰性图标堆砌；五颜六色超过 3 个主色。
- 模型天然偏安静极简——刻意往大胆推一档；好设计从用户的真实需求里长出来，
  风格库是没思路时的弹药，不是必选清单。
- 交付前自检 5 维：层级清晰度 / 字号反差 / 留白节奏 / 配色纪律 / 真实内容感。"""


def ensure_builtin_skills() -> bool:
    """注册/升级内置技能（幂等，永不抛错）。返回是否做了写入。"""
    try:
        import time as _t
        from hashmm.evolution.skill_manager import Skill, get_skill_manager
        mgr = get_skill_manager()
        mgr._load()
        marker = f"[builtin:{HUASHU_SKILL_VERSION}]"
        for s in mgr._skills:
            if s.id == HUASHU_SKILL_ID:
                if marker in s.prompt_template:
                    return False  # 同版本已注册：不动（保留质量分/使用数演化）
                # 版本升级：仅更新内容与触发词
                s.prompt_template = marker + "\n" + HUASHU_PROMPT
                s.trigger_patterns = list(HUASHU_TRIGGERS)
                s.description = "HTML 原生设计（原型/幻灯片/海报/信息图），改编自花叔Design"
                _persist(mgr, s)
                logger.info(f"[BuiltinSkills] huashu-design 升级到 {HUASHU_SKILL_VERSION}")
                return True
        skill = Skill(
            id=HUASHU_SKILL_ID,
            name="花叔Design·HTML原生设计",
            description="HTML 原生设计（原型/幻灯片/海报/信息图），改编自花叔Design",
            trigger_patterns=list(HUASHU_TRIGGERS),
            prompt_template=f"[builtin:{HUASHU_SKILL_VERSION}]\n" + HUASHU_PROMPT,
            quality_score=0.6,   # 起步分：可被真实用户反馈升降
            created_at=_t.time(),
        )
        _persist(mgr, skill, new=True)
        logger.info("[BuiltinSkills] huashu-design 已注册")
        return True
    except Exception as e:
        logger.warning(f"[BuiltinSkills] 注册失败（不影响主流程）: {e}")
        return False


def _persist(mgr, skill, new: bool = False) -> None:
    """落库 + 同步内存列表（与 SkillManager 的列式 skills 表对齐）。"""
    try:
        import json as _j
        from hashmm.api import database as _db
        with _db._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO skills
                   (id, name, description, trigger_patterns, prompt_template,
                    examples, quality_score, use_count, created_at, last_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill.id, skill.name, skill.description,
                 _j.dumps(skill.trigger_patterns, ensure_ascii=False),
                 skill.prompt_template,
                 _j.dumps(skill.examples, ensure_ascii=False),
                 skill.quality_score, skill.use_count,
                 skill.created_at, skill.last_used),
            )
        if new:
            mgr._skills.append(skill)
    except Exception as e:
        # V103.50: DB 损坏类错误只提示一次，避免每条对话刷屏（技能持久化失败不影响
        # 当前对话——技能已在内存注册）。其它错误正常告警。
        _msg = str(e)
        if "malformed" in _msg or "disk image" in _msg:
            if not getattr(_persist, "_warned_corrupt", False):
                logger.warning("[BuiltinSkills] 持久化暂不可用（DB 修复中）；不影响对话，"
                               "技能已在内存生效。后续 DB 恢复后会自动落库。")
                _persist._warned_corrupt = True
            if new:
                mgr._skills.append(skill)  # 内存仍注册
        else:
            logger.warning(f"[BuiltinSkills] 持久化失败: {e}")

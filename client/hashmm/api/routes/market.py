"""模板市场（V230 生态纪元·本地版）：出厂精品一键装进「我的模板」。

出厂清单内置于本文件（周报 / OKR / 项目复盘），自包含 html（样式+保存运行时），
不依赖外网；未来可指向自建 registry。安装即复制进用户模板库（重名自动改名）。
"""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth
from hashmm.api.routes.canvas_templates import _CAP, _LOCK, _load, _save

router = APIRouter(prefix="/api/market", tags=["market"])


def _shell(title: str, sub: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{--accent:#2563eb;--bg:#fff;--fg:#1a1a1a;--muted:#6b7280;--border:#e5e7eb}}
html.dark{{--bg:#0b0b0d;--fg:#ececf1;--muted:#9ca3af;--border:#27272a}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.75 Inter,'Noto Sans SC',sans-serif;padding:28px 30px 70px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--muted);font-size:12.5px;margin-bottom:16px}}
h2{{font-size:15px;margin:20px 0 8px;padding-left:9px;border-left:3px solid var(--accent)}}
ul{{margin:6px 0;padding-left:20px}}li{{margin:3px 0}}
.tag{{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;background:#2563eb22;color:var(--accent);margin-right:6px}}
#wc-body{{min-height:200px;outline:none}}</style></head><body>
<h1>{title}</h1><div class="sub">{sub}</div><div id="wc-body">{body}</div>
<script>(function(){{var t=null,ed=false;function post(m){{try{{window.parent.postMessage(m,"*")}}catch(e){{}}}}
window.addEventListener("message",function(e){{var m=e.data||{{}};
if(m.type==="wc:theme"){{document.documentElement.classList.toggle("dark",!!m.dark);if(m.accent)document.documentElement.style.setProperty("--accent",m.accent)}}
if(m.type==="wc:edit"){{ed=!!m.on;document.getElementById("wc-body").contentEditable=ed?"true":"false"}}
if(m.type==="wc:flush"){{post({{type:"wc:save",html:"<!DOCTYPE html>\\n"+document.documentElement.outerHTML}})}}}});
document.addEventListener("input",function(){{if(!ed)return;clearTimeout(t);t=setTimeout(function(){{post({{type:"wc:save",html:"<!DOCTYPE html>\\n"+document.documentElement.outerHTML}})}},1200)}});
post({{type:"wc:ready"}});}})();</script></body></html>"""


_ITEMS = [
    {"id": "mk-weekly", "name": "周报（市场精品）", "desc": "本周成果 · 数据 · 下周计划 · 需要支持",
     "html": _shell("周报", "本周成果 · 下周计划（可编辑，agent 可续写）",
        "<h2>本周成果</h2><ul><li><span class='tag'>完成</span>…</li></ul>"
        "<h2>关键数据</h2><ul><li>指标 A：__（环比 __%）</li></ul>"
        "<h2>下周计划</h2><ul><li>…</li></ul>"
        "<h2>需要的支持</h2><ul><li>…</li></ul>")},
    {"id": "mk-okr", "name": "OKR 制定（市场精品）", "desc": "目标 O · 关键结果 KR · 信心指数",
     "html": _shell("OKR", "季度目标与关键结果（可编辑，agent 可续写）",
        "<h2>O：一句话目标</h2><ul><li>…</li></ul>"
        "<h2>KR1</h2><ul><li>可量化结果 · 信心 __/10</li></ul>"
        "<h2>KR2</h2><ul><li>…</li></ul>"
        "<h2>风险与依赖</h2><ul><li>…</li></ul>")},
    {"id": "mk-retro", "name": "项目复盘（市场精品）", "desc": "做对了 · 做错了 · 下次改进",
     "html": _shell("项目复盘", "事实 → 分析 → 行动（可编辑，agent 可续写）",
        "<h2>背景与目标</h2><ul><li>…</li></ul>"
        "<h2>做对了什么</h2><ul><li>…</li></ul>"
        "<h2>做错/踩坑</h2><ul><li>…</li></ul>"
        "<h2>下次改进（行动项）</h2><ul><li>负责人 · 截止 · 动作</li></ul>")},
    {"id": "mk-meeting", "name": "会议纪要（市场精品）", "desc": "议题结论 · 决议 · 行动项认领",
     "html": _shell("会议纪要", "开完即发，行动项有主（可编辑，agent 可续写）",
        "<h2>基本信息</h2><ul><li>时间/参会：…</li></ul>"
        "<h2>议题与结论</h2><ul><li><span class='tag'>议题1</span>结论：…</li></ul>"
        "<h2>决议</h2><ul><li>…</li></ul>"
        "<h2>行动项</h2><ul><li>动作 · <b>负责人</b> · 截止 __/__</li></ul>"
        "<h2>未决事项</h2><ul><li>…（下次会议跟进）</li></ul>")},
    {"id": "mk-interview", "name": "面试评估（市场精品）", "desc": "维度评分 · 亮点疑虑 · 结论建议",
     "html": _shell("面试评估", "面完 10 分钟内写完（可编辑，agent 可续写）",
        "<h2>候选人</h2><ul><li>姓名/岗位/轮次：…</li></ul>"
        "<h2>维度评分（1-5）</h2><ul><li>技术深度：_ ｜ 工程素养：_ ｜ 沟通协作：_ ｜ 潜力：_</li></ul>"
        "<h2>亮点</h2><ul><li>…（附具体事例）</li></ul>"
        "<h2>疑虑</h2><ul><li>…（可追问点）</li></ul>"
        "<h2>结论建议</h2><ul><li><span class='tag'>强烈推荐 / 推荐 / 待定 / 不推荐</span>一句话理由</li></ul>")},
]


@router.get("/templates")
async def market_list(request: Request):
    require_auth(request)
    return {"items": [{"id": x["id"], "name": x["name"], "desc": x["desc"]} for x in _ITEMS]}


@router.post("/install")
async def market_install(request: Request):
    user = require_auth(request)
    body = await request.json()
    mid = str((body or {}).get("id") or "")
    src = next((x for x in _ITEMS if x["id"] == mid), None)
    if not src:
        raise HTTPException(404, "市场模板不存在")
    uid = user.get("uid", "")
    with _LOCK:
        items = _load(uid)
        if len(items) >= _CAP:
            raise HTTPException(409, f"模板已达上限 {_CAP} 个，先删几个再装")
        name = src["name"]
        names = {t.get("name") for t in items}
        base, n = name, 2
        while name in names:
            name = f"{base}-{n}"[:40]; n += 1
        items.append({"id": secrets.token_hex(4), "name": name, "html": src["html"],
                      "created": int(time.time())})
        _save(uid, items)
    return {"ok": True, "name": name}

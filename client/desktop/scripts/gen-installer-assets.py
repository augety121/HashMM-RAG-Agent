#!/usr/bin/env python3
"""gen-installer-assets.py — 生成微信级 NSIS 安装器品牌配图（V98）。

产出（desktop/build/）：
  installerSidebar.bmp    164×314  欢迎/完成页左侧品牌图（MUI_WELCOMEFINISHPAGE_BITMAP）
  uninstallerSidebar.bmp  164×314  卸载欢迎页变体（深灰紫，强调"数据默认保留"）
  installerHeader.bmp     150×57   目录/进度页右上角页眉图（MUI_HEADERIMAGE_BITMAP）
  installer-*.png                  同尺寸 PNG 预览（仅供审阅，不参与打包）

为什么是脚本而不是手画一次：品牌色/文案/版式以后要改，重跑一遍即可；
打包机（Windows）不需要跑它——BMP 已提交进 desktop/build/ 随仓库走。

NSIS 硬性要求：BMP 必须 24 位、无 alpha、无压缩（PIL 对 RGB 模式默认即是）。
高 DPI 下 NSIS 会拉伸位图——版式避免 1px 细节，文字 ≥11px。

字体：优先 Noto Sans CJK SC（沙箱 apt 可装 fonts-noto-cjk），缺失时退化为
DejaVu（中文字符会缺字形，此时改输出英文 tagline，不让脚本失败）。
"""
from __future__ import annotations

import os
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.normpath(os.path.join(HERE, "..", "build"))
ICON = os.path.join(BUILD, "icon.png")

# 品牌色（与前端主题一致）
BRAND = (109, 93, 246)        # #6d5df6
BRAND_LIGHT = (134, 119, 255) # 渐变上端
BRAND_DEEP = (66, 51, 196)    # 渐变下端
INK = (42, 47, 58)            # 页眉深灰字


# ───────────────────────── 字体 ─────────────────────────
def _pick_cjk(size: int, bold: bool = True):
    """在 Noto CJK ttc 里按字体名挑简体（SC）子字体；找不到返回 None。"""
    cands = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in cands:
        if not os.path.exists(path):
            continue
        for idx in range(6):
            try:
                f = ImageFont.truetype(path, size, index=idx)
                name = " ".join(f.getname())
                if "SC" in name or "Zen" in name:
                    return f
            except Exception:
                break
    return None


def _latin(size: int, bold: bool = True):
    p = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
         else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    try:
        return ImageFont.truetype(p, size)
    except Exception:
        return ImageFont.load_default()


CJK_OK = _pick_cjk(12) is not None


def font(size: int, bold: bool = True, cjk: bool = False):
    if cjk and CJK_OK:
        return _pick_cjk(size, bold)
    return _latin(size, bold)


# ───────────────────────── 绘图原语 ─────────────────────────
def vgrad(w: int, h: int, top, bottom, skew: float = 0.18) -> Image.Image:
    """带轻微对角偏移的纵向渐变（比纯纵向更有"大厂"质感）。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = min(1.0, max(0.0, (y + skew * x) / (h + skew * w)))
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
    return img


def soft_circle(canvas: Image.Image, cx: int, cy: int, r: int, alpha: int):
    """大尺寸半透明柔光圆——装饰层。"""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(layer)


def paste_icon_with_shadow(canvas: Image.Image, size: int, cx: int, cy: int):
    """把真品牌 icon 带柔影贴上去（不存在则画白底 H 占位）。"""
    if os.path.exists(ICON):
        ic = Image.open(ICON).convert("RGBA").resize((size, size), Image.LANCZOS)
    else:
        ic = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(ic)
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5,
                            fill=(255, 255, 255, 255))
        f = font(int(size * 0.62))
        d.text((size / 2, size / 2 - 2), "H", font=f, fill=BRAND + (255,), anchor="mm")
    # 柔影
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([cx - size // 2 + 2, cy - size // 2 + 5,
                          cx + size // 2 + 2, cy + size // 2 + 5],
                         radius=size // 5, fill=(20, 10, 60, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(sh)
    canvas.alpha_composite(ic, (cx - size // 2, cy - size // 2))


def dot_grid(canvas: Image.Image, x0: int, y0: int, cols: int, rows: int,
             gap: int, alpha: int):
    d = ImageDraw.Draw(canvas)
    for r in range(rows):
        for c in range(cols):
            d.ellipse([x0 + c * gap, y0 + r * gap,
                       x0 + c * gap + 2, y0 + r * gap + 2],
                      fill=(255, 255, 255, alpha))


# ───────────────────────── 侧栏图（164×314） ─────────────────────────
def make_sidebar(path_bmp: str, *, deep: bool, tagline: str, feats: list[str]):
    W, H = 164, 314
    if deep:  # 卸载变体：深灰紫，情绪降一档
        base = vgrad(W, H, (62, 66, 88), (32, 34, 46))
    else:
        base = vgrad(W, H, BRAND_LIGHT, BRAND_DEEP)
    img = base.convert("RGBA")

    # 柔光装饰
    soft_circle(img, -28, -20, 78, 26)
    soft_circle(img, W + 18, 132, 64, 18)
    soft_circle(img, 30, H - 26, 56, 14)
    dot_grid(img, W - 44, 196, 3, 3, 9, 40)

    # 品牌区
    paste_icon_with_shadow(img, 68, W // 2, 84)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 142), "HashMM", font=font(25), fill=(255, 255, 255, 255),
           anchor="mm")
    d.text((W // 2, 166), tagline, font=font(12, bold=False, cjk=True),
           fill=(255, 255, 255, 222), anchor="mm")
    # 分隔细线
    d.line([26, 186, W - 26, 186], fill=(255, 255, 255, 64), width=1)

    # 三条特性
    y = 206
    for t in feats:
        d.ellipse([24, y - 2, 29, y + 3], fill=(255, 255, 255, 235))
        d.text((38, y), t, font=font(11, bold=False, cjk=True),
               fill=(255, 255, 255, 235), anchor="lm")
        y += 26

    # 底部小字
    d.text((W // 2, H - 18), "hashmm desktop", font=font(10, bold=False),
           fill=(255, 255, 255, 130), anchor="mm")

    out = img.convert("RGB")
    out.save(path_bmp, "BMP")
    out.save(path_bmp.replace(".bmp", "-preview.png"), "PNG")


# ───────────────────────── 页眉图（150×57） ─────────────────────────
def make_header(path_bmp: str):
    W, H = 150, 57
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    # 右上柔淡品牌晕
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 54, -34, W + 40, 40], fill=BRAND + (34,))
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(glow)

    paste_icon_with_shadow(img, 30, 26, H // 2 - 3)
    d = ImageDraw.Draw(img)
    d.text((48, H // 2 - 9), "HashMM", font=font(16), fill=INK + (255,), anchor="lm")
    d.text((48, H // 2 + 9), "Local-first AI", font=font(9, bold=False),
           fill=(120, 126, 142, 255), anchor="lm")
    # 底部品牌渐变条
    for x in range(W):
        t = x / W
        col = tuple(int(a + (b - a) * t) for a, b in zip(BRAND_LIGHT, BRAND_DEEP))
        d.line([x, H - 3, x, H], fill=col + (255,))

    out = img.convert("RGB")
    out.save(path_bmp, "BMP")
    out.save(path_bmp.replace(".bmp", "-preview.png"), "PNG")


# ───────────────────── 欢迎整页画布（V100 · 微信式单图全屏） ─────────────────────
def make_welcome_canvas(path_bmp: str):
    """整页就是一张设计好的图（微信图3的精髓）：493×312 铺满 MUI 全窗页客户区，
    logo 居中 + 品牌渐变大按钮 + 协议行 + **底部安装路径行/所需空间** 全"烤"进图里，
    NSIS 侧只贴这一张图 + 一个真勾选框 + 一个只读路径 label（显示 $INSTDIR）。
    按钮/协议链接靠点击坐标 hit-test。几何由本函数内联回写进 installer.nsh 的
    HM_GEOMETRY 标记块——像素坐标单一事实来源，自包含分发。"""
    W, H, SS = 493, 312, 4
    img = Image.new("RGBA", (W * SS, H * SS), (255, 255, 255, 255))

    # 底部极轻的冷灰渐变（微信式克制：几乎看不见，但页面不"死白"）
    g = Image.new("L", (1, H * SS))
    for y in range(H * SS):
        t = max(0.0, (y / (H * SS) - 0.6)) / 0.4
        g.putpixel((0, y), int(9 * t))
    tint = Image.new("RGBA", img.size, (116, 119, 142, 255))
    tint.putalpha(g.resize(img.size))
    img.alpha_composite(tint)

    # logo 背后一团极淡品牌晕
    logo_cy = 70
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [(W // 2 - 62) * SS, (logo_cy - 62) * SS, (W // 2 + 62) * SS, (logo_cy + 62) * SS],
        fill=BRAND + (18,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(15 * SS)))

    # 居中 logo（78px，柔影）—— 比旧版小，给底部路径行腾地方
    LR = 39
    if os.path.exists(ICON):
        ic = Image.open(ICON).convert("RGBA").resize((LR * 2 * SS, LR * 2 * SS), Image.LANCZOS)
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [(W // 2 - LR + 2) * SS, (logo_cy - LR + 4) * SS,
             (W // 2 + LR + 2) * SS, (logo_cy + LR + 4) * SS],
            radius=16 * SS, fill=(54, 44, 120, 60))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6 * SS)))
        img.alpha_composite(ic, ((W // 2 - LR) * SS, (logo_cy - LR) * SS))
    else:
        d0 = ImageDraw.Draw(img)
        d0.rounded_rectangle([(W // 2 - LR) * SS, (logo_cy - LR) * SS,
                              (W // 2 + LR) * SS, (logo_cy + LR) * SS],
                             radius=16 * SS, fill=BRAND + (255,))
        d0.text((W // 2 * SS, logo_cy * SS), "H", font=font(46 * SS),
                fill=(255, 255, 255, 255), anchor="mm")

    d = ImageDraw.Draw(img)
    # 产品名 + 标语
    d.text((W // 2 * SS, 128 * SS), "HashMM", font=_latin(22 * SS, bold=True),
           fill=(31, 31, 39, 255), anchor="mm")
    tag = "本地优先 · AI 工作台" if CJK_OK else "Local-first AI Workbench"
    d.text((W // 2 * SS, 150 * SS), tag, font=font(10 * SS, bold=False, cjk=True),
           fill=(138, 143, 158, 255), anchor="mm")

    # 主按钮（210×40 药丸，品牌渐变 + 柔影 + 釉面）
    BW, BH = 210, 40
    bl, bt = (W - BW) // 2, 170
    br, bb = bl + BW, bt + BH
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [(bl + 3) * SS, (bt + 6) * SS, (br - 3) * SS, (bb + 4) * SS],
        radius=(BH // 2) * SS, fill=(77, 62, 205, 80))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4 * SS)))
    grad = vgrad(BW * SS, BH * SS, BRAND_LIGHT, BRAND_DEEP, skew=0.06).convert("RGBA")
    mask = Image.new("L", grad.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, grad.size[0] - 1, grad.size[1] - 1],
                                           radius=(BH // 2) * SS, fill=255)
    img.paste(grad, (bl * SS, bt * SS), mask)
    hi = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle(
        [(bl + 2) * SS, (bt + 1) * SS, (br - 2) * SS, (bt + BH // 2) * SS],
        radius=(BH // 2 - 2) * SS, fill=(255, 255, 255, 22))
    img.alpha_composite(hi)
    btn_label = "立即安装" if CJK_OK else "Install Now"
    d.text(((bl + br) // 2 * SS, (bt + bb) // 2 * SS - SS), btn_label,
           font=font(14 * SS, bold=True, cjk=True), fill=(255, 255, 255, 255),
           anchor="mm")

    # 协议行（勾选框留 14px 空位给真控件，文字烤进图）
    cb_s, gap = 14, 7
    row_cy = 226
    f_sm = font(10 * SS, bold=False, cjk=True)
    t_agree = "已阅读并同意 " if CJK_OK else "I have read and agree to "
    t_link = "《用户协议与隐私政策》" if CJK_OK else "the Terms & Privacy Policy"
    w_agree = d.textlength(t_agree, font=f_sm)
    w_link = d.textlength(t_link, font=f_sm)
    row_w = cb_s * SS + gap * SS + w_agree + w_link
    rx = (W * SS - row_w) // 2
    cb_x = int(round(rx / SS))
    cb_y = row_cy - cb_s // 2 - 1
    tx = rx + cb_s * SS + gap * SS
    d.text((tx, row_cy * SS), t_agree, font=f_sm, fill=(107, 112, 128, 255), anchor="lm")
    lx = tx + w_agree
    d.text((lx, row_cy * SS), t_link, font=f_sm, fill=BRAND + (255,), anchor="lm")

    # ── 底部：微信式安装路径行 + 所需空间（路径值由 NSIS 用只读 label 叠 $INSTDIR）──
    ML, MR = 42, W - 42
    div_y = 250
    d.line([(ML * SS, div_y * SS), (MR * SS, div_y * SS)], fill=(234, 234, 240, 255), width=SS)
    f_lbl = font(10 * SS, bold=False, cjk=True)
    # "安装路径" 标签
    path_label = "安装路径" if CJK_OK else "Install to"
    d.text((ML * SS, 270 * SS), path_label, font=f_lbl, fill=(120, 124, 138, 255), anchor="lm")
    lbl_w = d.textlength(path_label, font=f_lbl)
    # 路径框（浅边浅底的输入框样式；真值由 NSIS 叠上去）
    pf_l = int(ML + lbl_w / SS + 12)
    pf_r = MR
    pf_t, pf_b = 261, 280
    d.rounded_rectangle([pf_l * SS, pf_t * SS, pf_r * SS, pf_b * SS],
                        radius=4 * SS, fill=(247, 247, 250, 255), outline=(225, 225, 232, 255), width=SS)
    # 所需空间（小灰字）
    space_txt = "安装所需空间：约 540 MB" if CJK_OK else "Space required: ~540 MB"
    d.text((ML * SS, 296 * SS), space_txt, font=font(9 * SS, bold=False, cjk=True),
           fill=(168, 172, 184, 255), anchor="lm")

    geo = {
        "W": W, "H": H,
        "BTN_L": bl, "BTN_T": bt, "BTN_R": br, "BTN_B": bb,
        "LINK_L": int(lx // SS), "LINK_T": row_cy - 9,
        "LINK_R": int((lx + w_link) // SS) + 1, "LINK_B": row_cy + 9,
        "CB_X": cb_x, "CB_Y": cb_y, "CB_S": cb_s,
        # 只读路径 label 的落点与可用宽度（NSIS 叠 $INSTDIR 文本）
        "PATH_X": pf_l + 6, "PATH_Y": 263, "PATH_W": pf_r - pf_l - 12, "PATH_H": 16,
    }
    out = img.resize((W, H), Image.LANCZOS).convert("RGB")
    out.save(path_bmp, "BMP")
    out.save(path_bmp.replace(".bmp", "-preview.png"), "PNG")
    # V100：几何**内联回写**进 installer.nsh 的 HM_GEOMETRY 标记块（不再写独立
    # .nsh —— 二级 include 在真机分发会丢失，曾致打包失败）。单一事实来源仍在此处。
    _write_geometry_into_nsh(geo)
    return geo


def _write_geometry_into_nsh(geo: dict):
    """把 geo 写进 desktop/build/installer.nsh 的 HM_GEOMETRY 标记块之间。
    标记缺失则报错（绝不静默丢几何）。"""
    nsh_path = os.path.join(BUILD, "installer.nsh")
    BEGIN = "; >>> HM_GEOMETRY"
    END = "; <<< HM_GEOMETRY <<<"
    if not os.path.exists(nsh_path):
        raise SystemExit(f"installer.nsh 不存在，无法回写几何: {nsh_path}")
    src = open(nsh_path, encoding="utf-8").read()
    i = src.find(BEGIN)
    j = src.find(END)
    if i < 0 or j < 0 or j < i:
        raise SystemExit("installer.nsh 缺少 HM_GEOMETRY 标记块，拒绝回写（避免丢几何）")
    # 保留 BEGIN 行本身（含说明），替换其后到 END 行之前的内容
    begin_line_end = src.find("\n", i) + 1
    block = "".join(f"!define HM_GEO_{k} {v}\n" for k, v in geo.items())
    new = src[:begin_line_end] + block + src[j:]
    if new != src:
        open(nsh_path, "w", encoding="utf-8").write(new)
        print(f"  ↻ 几何已回写进 installer.nsh（{len(geo)} 个 define）")
    else:
        print(f"  = installer.nsh 几何已最新（{len(geo)} 个 define）")


def main():
    os.makedirs(BUILD, exist_ok=True)
    make_sidebar(os.path.join(BUILD, "installerSidebar.bmp"), deep=False,
                 tagline="本地优先 · AI 工作台" if CJK_OK else "Local-first AI",
                 feats=(["完全本地运行", "数据不出本机", "卸载保留数据"] if CJK_OK
                        else ["Runs fully local", "Data stays here", "Keeps your data"]))
    make_sidebar(os.path.join(BUILD, "uninstallerSidebar.bmp"), deep=True,
                 tagline="感谢使用 HashMM" if CJK_OK else "Thanks for using",
                 feats=(["数据默认保留", "重装即恢复", "期待再见"] if CJK_OK
                        else ["Data is kept", "Reinstall restores", "See you again"]))
    make_header(os.path.join(BUILD, "installerHeader.bmp"))
    geo = make_welcome_canvas(os.path.join(BUILD, "installerWelcome.bmp"))

    # V100：旧的拼控件资产已废弃（整页一张图取代），清掉避免死资产
    for stale in ("installerWelcomeLogo.bmp", "installerWelcomeLogo-preview.png",
                  "installerBtnInstall.bmp", "installerBtnInstall-preview.png"):
        p = os.path.join(BUILD, stale)
        if os.path.exists(p):
            os.remove(p)
            print(f"  rm 死资产 {stale}")

    # 自检：NSIS 要求 24 位 BMP，尺寸必须精确
    for name, (w, h) in [("installerSidebar.bmp", (164, 314)),
                         ("uninstallerSidebar.bmp", (164, 314)),
                         ("installerHeader.bmp", (150, 57)),
                         ("installerWelcome.bmp", (493, 312))]:
        p = os.path.join(BUILD, name)
        im = Image.open(p)
        assert im.size == (w, h), f"{name} 尺寸错: {im.size}"
        assert im.mode == "RGB", f"{name} 不是 24 位: {im.mode}"
        with open(p, "rb") as f:
            head = f.read(30)
        assert head[:2] == b"BM" and head[28] == 24, f"{name} BMP 头不合规"
        print(f"  ok {name}  {w}x{h}  24bit  {os.path.getsize(p)//1024}KB")
    # 几何自检：矩形必须落在画布内，12 个 define 一个不能少
    # 几何已内联进 installer.nsh —— 从那里校验，并清掉可能残留的旧独立文件
    stale_geo = os.path.join(BUILD, "hm-welcome-geometry.nsh")
    if os.path.exists(stale_geo):
        os.remove(stale_geo)
        print("  rm 旧独立几何文件 hm-welcome-geometry.nsh（已内联）")
    nsh_txt = open(os.path.join(BUILD, "installer.nsh"), encoding="utf-8").read()
    for k in geo:
        assert f"!define HM_GEO_{k} " in nsh_txt, f"installer.nsh 几何缺 {k}"
    assert 'hm-welcome-geometry' not in nsh_txt.split("\n")[0:0+0] or True  # noqa
    assert "!include" not in "".join(
        l for l in nsh_txt.splitlines() if "hm-welcome-geometry" in l and "!include" in l), \
        "installer.nsh 不应再 !include 独立几何文件"
    assert 0 < geo["BTN_L"] < geo["BTN_R"] < geo["W"]
    assert 0 < geo["LINK_L"] < geo["LINK_R"] <= geo["W"]
    assert 0 < geo["CB_X"] < geo["LINK_L"]
    assert 0 < geo["PATH_X"] < geo["W"] and geo["PATH_W"] > 0  # 只读路径 label 落点
    print(f"  ok installer.nsh 内联几何  {len(geo)} defines（无二级 include）")
    print("CJK 字体:", "Noto Sans CJK SC" if CJK_OK else "缺失（已退化英文文案）")


if __name__ == "__main__":
    sys.exit(main())

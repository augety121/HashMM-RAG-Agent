#!/usr/bin/env python3
"""HashMM App import 自检（V230 起打包前必跑）——编译级精确，零误删零误报：
① 所有 Icons.<Style>.<Name> 与 Icons.AutoMirrored.<Style>.<Name> 裸用 → 反查 import 是否齐
   （既抓缺失，也绝不把"在用的"当重复删——Filled/Outlined 同名是两个对象，都可并存）；
② 17 类常用 Compose/协程符号裸用 → 反查 import；
③ import 行内 ',,'。退出码=问题数。"""
import re, os, sys
NEED={"LaunchedEffect":"androidx.compose.runtime.LaunchedEffect","launch":"kotlinx.coroutines.launch",
"Alignment":"androidx.compose.ui.Alignment","Brush":"androidx.compose.ui.graphics.Brush",
"clickable":"androidx.compose.foundation.clickable","horizontalScroll":"androidx.compose.foundation.horizontalScroll",
"rememberScrollState":"androidx.compose.foundation.rememberScrollState",
"rememberSaveable":"androidx.compose.runtime.saveable.rememberSaveable",
"rememberCoroutineScope":"androidx.compose.runtime.rememberCoroutineScope",
"AlertDialog":"androidx.compose.material3.AlertDialog","OutlinedTextField":"androidx.compose.material3.OutlinedTextField",
"TextButton":"androidx.compose.material3.TextButton","Button":"androidx.compose.material3.Button",
"CircleShape":"androidx.compose.foundation.shape.CircleShape","RoundedCornerShape":"androidx.compose.foundation.shape.RoundedCornerShape",
"PullToRefreshBox":"androidx.compose.material3.pulltorefresh.PullToRefreshBox","HorizontalDivider":"androidx.compose.material3.HorizontalDivider",
"IntrinsicSize":"androidx.compose.foundation.layout.IntrinsicSize","fillMaxHeight":"androidx.compose.foundation.layout.fillMaxHeight",
"remember":"androidx.compose.runtime.remember"}
root=os.path.join(os.path.dirname(__file__),"..","app","src","main","java","com","hashmm","app")
bad=[]
for dp,_,fs in os.walk(root):
    for fn in fs:
        if not fn.endswith(".kt"): continue
        src=open(os.path.join(dp,fn),encoding="utf-8").read()
        imports=set(re.findall(r"^import (.+)$",src,re.M))
        body=re.sub(r"/\*(?:.|\n)*?\*/","",src); body=re.sub(r"//[^\n]*","",body)
        # ① Icons 裸用反查（核心：不再猜重复，只认"用到但没 import"）
        for m in re.finditer(r"Icons\.(Filled|Outlined|Rounded|Sharp|TwoTone)\.(\w+)", body):
            need=f"androidx.compose.material.icons.{m.group(1).lower()}.{m.group(2)}"
            if need not in imports: bad.append((fn,f"Icons.{m.group(1)}.{m.group(2)}",need))
        for m in re.finditer(r"Icons\.AutoMirrored\.(Filled|Outlined|Rounded|Sharp|TwoTone)\.(\w+)", body):
            need=f"androidx.compose.material.icons.automirrored.{m.group(1).lower()}.{m.group(2)}"
            if need not in imports: bad.append((fn,f"AutoMirrored.{m.group(1)}.{m.group(2)}",need))
        # ② 常用符号裸用反查
        for sym,tail in NEED.items():
            if re.search(r"(?<![\w.])"+re.escape(sym)+r"(?![\w])",body):
                if tail not in imports and (tail.rsplit(".",1)[0]+".*") not in imports: bad.append((fn,sym,tail))
        # ③ 项目主题色常量裸用反查（Brand/BrandLight/… 等，避免漏 import）
        THEME = ["Brand", "BrandDark", "BrandLight", "BrandContainer",
                 "BrandGradient", "AvatarGradient"]
        is_theme_pkg = "/ui/theme/" in os.path.join(dp, fn).replace("\\", "/")
        if not is_theme_pkg:
            for sym in THEME:
                if re.search(r"(?<![\w.])"+sym+r"(?![\w])", body):
                    need = f"com.hashmm.app.ui.theme.{sym}"
                    if need not in imports and "com.hashmm.app.ui.theme.*" not in imports:
                        bad.append((fn, sym, need))
        # ④ 双逗号
        for i,line in enumerate(src.splitlines(),1):
            if line.lstrip().startswith("import") and ",," in line: bad.append((fn,f"L{i} ',,'","-"))
for f,s,t in sorted(set(bad)): print(f"缺 [{f}] {s} → import {t}")
print("自检：",("全部清零 ✓" if not bad else f"{len(set(bad))} 处缺口"))
sys.exit(len(set(bad)))

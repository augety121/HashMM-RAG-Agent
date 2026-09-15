#!/usr/bin/env python3
"""HashMM App import 自检（V235 起打包前必跑）：常用 Compose/协程符号 vs import 缺失。
strip 字符串/行注释/块注释后按词边界匹配；Icons.Outlined/AutoMirrored 逐图标核对。退出码=缺口数。"""
import re, os, sys
def strip(src):
    src=re.sub(r'"""(?:.|\n)*?"""','""',src); src=re.sub(r'"(?:\\.|[^"\\\n])*"','""',src)
    src=re.sub(r"/\*(?:.|\n)*?\*/","",src); return re.sub(r"//[^\n]*","",src)
NEED={"LaunchedEffect":"androidx.compose.runtime.LaunchedEffect","launch":"kotlinx.coroutines.launch",
"Alignment":"androidx.compose.ui.Alignment","Brush":"androidx.compose.ui.graphics.Brush",
"clickable":"androidx.compose.foundation.clickable","horizontalScroll":"androidx.compose.foundation.horizontalScroll",
"rememberScrollState":"androidx.compose.foundation.rememberScrollState",
"rememberSaveable":"androidx.compose.runtime.saveable.rememberSaveable",
"rememberCoroutineScope":"androidx.compose.runtime.rememberCoroutineScope",
"AlertDialog":"androidx.compose.material3.AlertDialog","OutlinedTextField":"androidx.compose.material3.OutlinedTextField",
"TextButton":"androidx.compose.material3.TextButton","Button":"androidx.compose.material3.Button",
"CircleShape":"androidx.compose.foundation.shape.CircleShape","RoundedCornerShape":"androidx.compose.foundation.shape.RoundedCornerShape",
"PullToRefreshBox":"androidx.compose.material3.pulltorefresh.PullToRefreshBox"}
root=os.path.join(os.path.dirname(__file__),"..","app","src","main","java","com","hashmm","app")
bad=[]
for dp,_,fs in os.walk(root):
    for fn in fs:
        if not fn.endswith(".kt"): continue
        src=open(os.path.join(dp,fn),encoding="utf-8").read()
        imports=set(re.findall(r"^import (.+)$",src,re.M)); body=strip(src)
        for sym,tail in NEED.items():
            if re.search(r"(?<![\w.])"+re.escape(sym)+r"(?![\w])",body):
                if tail not in imports and (tail.rsplit(".",1)[0]+".*") not in imports: bad.append((fn,sym,tail))
        for x in set(re.findall(r"Icons\.Outlined\.(\w+)",body)):
            t=f"androidx.compose.material.icons.outlined.{x}"
            if t not in imports: bad.append((fn,f"Outlined.{x}",t))
        for x in set(re.findall(r"Icons\.AutoMirrored\.Outlined\.(\w+)",body)):
            t=f"androidx.compose.material.icons.automirrored.outlined.{x}"
            if t not in imports: bad.append((fn,f"AutoMirrored.{x}",t))
for f,s,t in sorted(set(bad)): print(f"缺 [{f}] {s} → import {t}")
print("自检：",("全部清零 ✓" if not bad else f"{len(set(bad))} 处缺口"))
sys.exit(len(set(bad)))

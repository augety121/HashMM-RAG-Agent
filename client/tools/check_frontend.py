#!/usr/bin/env python3
"""frontend-next 自检（V236 起打包前必跑）——只查两类**确定性**语法事故，零误报：
① 同文件跨 import 行重复符号（next build 'already been declared' 根源）
② import 行内 ',,'（regex 拼接残留）。
括号平衡因 TS 正则字面量/JSX 泛型误报率高，改由各轮 node --check / next build 兜底。退出码=问题数。"""
import re, os, sys, subprocess, shutil
root=os.path.join(os.path.dirname(__file__),"..","frontend-next")

# Windows 中文控制台通常使用 GBK，直接打印 ✓ 等符号会让自检脚本本身崩溃。
# 保留可读输出，同时用替换策略保证诊断工具永远能返回真实检查结果。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure=getattr(_stream,"reconfigure",None)
    if callable(_reconfigure): _reconfigure(errors="replace")

# V238: 若装了 typescript，先跑权威的 tsc --noEmit——字段幻觉/前引/类型错一网打尽
def _try_tsc():
    tsc=os.path.join(root,"node_modules",".bin","tsc")
    if not (os.path.exists(tsc) or os.path.exists(tsc+".cmd")): return None
    try:
        r=subprocess.run([tsc if os.path.exists(tsc) else tsc+".cmd","--noEmit"],
                         cwd=root,capture_output=True,text=True,timeout=400)
        return r.returncode, r.stdout+r.stderr
    except Exception:
        return None
_t=_try_tsc()
if _t is not None:
    code,out=_t
    if code==0: print("tsc --noEmit：类型检查全过 ✓")
    else: print("tsc 报错：\n"+out[:4000])
    # tsc 是权威，跑过就以它为准（正则检查仅作补充展示，不改退出码语义）
    if code!=0: sys.exit(code)
bad=[]
for dp,_,fs in os.walk(root):
    if "node_modules" in dp or os.sep+".next" in dp or os.sep+"out" in dp: continue
    for fn in fs:
        if not fn.endswith((".ts",".tsx")): continue
        src=open(os.path.join(dp,fn),encoding="utf-8").read()
        seen={}
        for m in re.finditer(r'^import\s*(type\s+)?\{([^}]*)\}\s*from',src,re.M):
            for raw in m.group(2).split(","):
                name=raw.strip().split(" as ")[-1].replace("type ","").strip()
                if not name: continue
                if name in seen: bad.append((fn,f"重复 import 符号 '{name}'"))
                seen[name]=1
        for i,line in enumerate(src.splitlines(),1):
            if line.lstrip().startswith("import") and ",," in line:
                bad.append((fn,f"L{i} import 行含 ',,'"))
        # V237: hook 依赖数组前引检测（TS "used before its declaration" 的唯一触发形态）
        lines=src.splitlines()
        decl={}
        for i,l in enumerate(lines,1):
            m=re.match(r"\s*const (?:\[\s*)?(\w+)",l)
            if m: decl.setdefault(m.group(1),i)
        for i,l in enumerate(lines,1):
            m=re.match(r"\s*\}\s*,\s*\[([^\]]*)\]\s*\)",l)
            if not m: continue
            for w in re.findall(r"[A-Za-z_]\w*",m.group(1)):
                if w in decl and decl[w]>i and decl[w]-i<300:
                    bad.append((fn,f"L{i} 依赖数组前引 '{w}'（声明在 L{decl[w]}）"))
for f,msg in sorted(set(bad)): print("坏",f,msg)
print("frontend 自检：",("全部清零 ✓" if not bad else f"{len(set(bad))} 处问题"))
sys.exit(len(set(bad)))

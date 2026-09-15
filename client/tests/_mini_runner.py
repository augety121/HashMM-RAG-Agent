"""Minimal pytest-compatible runner for sandbox verification (no pytest installed).

Supports: test_* collection, fixtures (incl. yield), monkeypatch, parametrize,
mark.skip via conftest's pytest_collection_modifyitems. NOT a pytest replacement —
just enough to verify our test logic runs green in the sandbox. On the real machine,
`python -m pytest` runs these same files for real.
"""
import sys, os, types, inspect, traceback, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ---- fake pytest module ----
pytest = types.ModuleType("pytest")
_SKIP = type("Skipped", (Exception,), {})
class _Mark:
    def __init__(self, name): self.name=name
    def __call__(self, *a, **k):
        # parametrize / skip decorators
        if self.name == "parametrize":
            argnames, argvals = a[0], a[1]
            def deco(fn):
                fn._parametrize = (argnames, argvals)
                return fn
            return deco
        if self.name == "skip":
            def deco(fn):
                fn._skip = k.get("reason", "skip")
                return fn
            return deco
        def deco(fn):
            marks = getattr(fn, "_marks", set()); marks.add(self.name); fn._marks=marks
            return fn
        return deco
class _MarkGen:
    def __getattr__(self, name): return _Mark(name)
pytest.mark = _MarkGen()
def _skip(reason=""): raise _SKIP(reason)
pytest.skip = _skip
def _importorskip(modname, reason=None):
    """pytest.importorskip 兼容：导入失败时按 skip 处理（沙箱缺 fastapi 等重依赖）。"""
    import importlib
    try:
        return importlib.import_module(modname)
    except Exception as e:  # ImportError 及其连带（如依赖的依赖缺失）
        raise _SKIP(reason or f"importorskip({modname}): {e}")
pytest.importorskip = _importorskip
def _fixture(fn=None, **kw):
    def wrap(f):
        f._is_fixture=True; f._autouse=kw.get("autouse",False)
        f._scope=kw.get("scope","function")   # V311: autouse 需按 scope 区分执行
        return f
    return wrap(fn) if fn else wrap
pytest.fixture = _fixture
sys.modules["pytest"] = pytest

# ---- monkeypatch ----
class MonkeyPatch:
    def __init__(self): self._undo=[]; self._cwd=None
    def setenv(self,k,v): self._undo.append(("env",k,os.environ.get(k))); os.environ[k]=str(v)
    def delenv(self,k,raising=True):
        self._undo.append(("env",k,os.environ.get(k))); os.environ.pop(k,None)
    def setattr(self,target,name,value=None,raising=True):
        # 兼容两种签名：setattr(obj, "name", val) 与 setattr("pkg.mod.attr", val)
        if isinstance(target,str):
            value=name
            modpath,_,name=target.rpartition(".")
            import importlib
            target=importlib.import_module(modpath)
        had=hasattr(target,name)
        old=getattr(target,name,None)
        if raising and not had:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        self._undo.append(("attr",(target,name,had),old))
        setattr(target,name,value)
    def delattr(self,target,name=None,raising=True):
        if isinstance(target,str):
            modpath,_,name=target.rpartition(".")
            import importlib
            target=importlib.import_module(modpath)
        had=hasattr(target,name)
        old=getattr(target,name,None)
        if not had:
            if raising: raise AttributeError(f"{target!r} has no attribute {name!r}")
            return
        self._undo.append(("attr",(target,name,had),old))
        delattr(target,name)
    def chdir(self,path):
        import os as _o
        self._cwd=_o.getcwd(); _o.chdir(str(path))
    def undo(self):
        if self._cwd:
            import os as _o
            try: _o.chdir(self._cwd)
            except Exception: pass
            self._cwd=None
        for kind,k,old in reversed(self._undo):
            if kind=="env":
                if old is None: os.environ.pop(k,None)
                else: os.environ[k]=old
            elif kind=="attr":
                obj,name,had=k
                if had: setattr(obj,name,old)
                else:
                    try: delattr(obj,name)
                    except Exception: pass
        self._undo=[]

class _ModuleExitError(Exception):
    """测试文件在 import 期间调用了 sys.exit()——这是配置错误，按失败暴露。"""


def run_file(path):
    # ★ V311：conftest 必须先于测试模块加载（pytest 的真实语义）。
    #   此前顺序颠倒：测试模块先 exec、conftest（负责把项目根塞进 sys.path）后加载，
    #   导致按字母序排第一的测试文件在模块级 `import hashmm` 必然 ModuleNotFoundError，
    #   被误判成"沙箱缺依赖"整文件跳过（test_adaptive_rag 就这样假跳过）。
    conf_mod = None
    conf_path = path.parent/"conftest.py"
    if conf_path.exists():
        cspec = importlib.util.spec_from_file_location("conftest", conf_path)
        conf_mod = importlib.util.module_from_spec(cspec)
        cspec.loader.exec_module(conf_mod)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    # V308 修 P0-6：exec_module 若在模块【顶层】遇到 sys.exit()，会抛 SystemExit。
    # SystemExit 继承 BaseException 而非 Exception，原先主循环的 `except Exception`
    # 抓不到它 → 整个 runner 进程被 exit(0) 提前终止、退出码 0 → 后续测试文件全部
    # 未运行却报“成功”，形成假绿。这里显式捕获并转成普通异常向上抛，让主循环按
    # “该文件失败”统计（而不是让整个套件静默通过）。
    try:
        spec.loader.exec_module(mod)
    except SystemExit as se:
        raise _ModuleExitError(
            f"{path.name} 在 import 期间调用了 sys.exit({se.code!r})——"
            f"测试文件不应在顶层退出进程"
        ) from se
    fixtures = {n:f for n,f in vars(mod).items() if getattr(f,"_is_fixture",False)}
    # conftest fixtures（模块自己的同名 fixture 优先）
    if conf_mod is not None:
        for n,f in vars(conf_mod).items():
            if getattr(f,"_is_fixture",False): fixtures.setdefault(n,f)
    tests = [(n,f) for n,f in vars(mod).items() if n.startswith("test_") and callable(f)]
    passed=failed=skipped=0; fails=[]
    for name,fn in tests:
        cases = [({}, "")]
        if hasattr(fn,"_parametrize"):
            argnames, argvals = fn._parametrize
            names = [a.strip() for a in argnames.split(",")] if isinstance(argnames,str) else argnames
            cases = []
            for v in argvals:
                if len(names)==1: cases.append(({names[0]:v}, repr(v)))
                else: cases.append((dict(zip(names,v)), repr(v)))
        for pkw, label in cases:
            if hasattr(fn,"_skip"): skipped+=1; continue
            mp=MonkeyPatch(); gens=[]
            try:
                sig = inspect.signature(fn); kwargs=dict(pkw)
                # ★ V311：真正执行 autouse fixture。此前第 50 行只记录 _autouse 标记、
                #   从未运行，导致靠 autouse 做环境准备的测试在沙箱里假失败
                #   （test_tool_retrieval 的 _reset 没跑 → 检索开关没开 → 断言必挂）。
                #   仅执行 function 级；session 级维持旧行为（跳过），避免连带风险。
                for _an, _af in fixtures.items():
                    if not getattr(_af, "_autouse", False): continue
                    if getattr(_af, "_scope", "function") != "function": continue
                    if _an in sig.parameters: continue      # 已按参数注入的不双跑
                    _ask = inspect.signature(_af); _afk = {}
                    if "monkeypatch" in _ask.parameters: _afk["monkeypatch"] = mp
                    if "tmp_path" in _ask.parameters:
                        import tempfile as _tf2
                        _afk["tmp_path"] = Path(_tf2.mkdtemp(prefix="mtp_"))
                    _r = _af(**_afk)
                    if inspect.isgenerator(_r):
                        gens.append(_r); next(_r)
                for pn in sig.parameters:
                    if pn in pkw: continue
                    if pn=="monkeypatch": kwargs[pn]=mp; continue
                    if pn=="tmp_path":
                        import tempfile as _tf
                        kwargs[pn]=Path(_tf.mkdtemp(prefix="mtp_")); continue
                    if pn in fixtures:
                        fx=fixtures[pn]; fsig=inspect.signature(fx); fkw={}
                        if "monkeypatch" in fsig.parameters: fkw["monkeypatch"]=mp
                        if "tmp_db" in fsig.parameters and "tmp_db" in fixtures and pn!="tmp_db":
                            # nested fixture tmp_db
                            tdb=fixtures["tmp_db"]; tsig=inspect.signature(tdb); tkw={}
                            if "monkeypatch" in tsig.parameters: tkw["monkeypatch"]=mp
                            g=tdb(**tkw); val=next(g) if inspect.isgenerator(g) else g
                            gens.append(g); fkw["tmp_db"]=val
                        res=fx(**fkw)
                        if inspect.isgenerator(res): gens.append(res); kwargs[pn]=next(res)
                        else: kwargs[pn]=res
                fn(**kwargs)
                passed+=1
            except _SKIP: skipped+=1
            except Exception as e:
                failed+=1; fails.append((f"{name}[{label}]" if label else name, traceback.format_exc().splitlines()[-1]))
            finally:
                for g in reversed(gens):
                    try: next(g)
                    except StopIteration: pass
                    except Exception: pass
                mp.undo()
    return passed,failed,skipped,fails

if __name__=="__main__":
    total_p=total_f=total_s=0
    # 沙箱缺失的依赖/资源 → 整文件跳过（真机 pytest 会真跑）
    SANDBOX_SKIP = {"test_contracts_api.py", "test_retrieval_regression.py"}
    for tf in sorted(ROOT.glob("test_*.py")):
        if tf.name in SANDBOX_SKIP:
            print(f"  ○ {tf.name}: SKIP（沙箱缺 fastapi/data，真机 pytest 真跑）")
            continue
        try:
            p,f,s,fails=run_file(tf)
        except _ModuleExitError as e:
            # 顶层 sys.exit：真实配置错误，计为失败并让套件非 0 退出（不再假绿）。
            print(f"  ✗ {tf.name}: FAIL（{e}）")
            total_f += 1
            continue
        except (SyntaxError, IndentationError) as e:
            # 语法错误也是真实失败，不能当“缺依赖”跳过。
            print(f"  ✗ {tf.name}: FAIL（{type(e).__name__}: {e}）")
            total_f += 1
            continue
        except (ImportError, ModuleNotFoundError) as e:
            # 仅【导入缺失】才按沙箱跳过（真机 pytest 有完整依赖会真跑）。
            print(f"  ○ {tf.name}: SKIP（{type(e).__name__}: {e} —— 沙箱缺依赖，真机 pytest 真跑）")
            continue
        except Exception as e:
            # 其余未预期异常：保守暴露为失败而非跳过，避免再次掩盖问题。
            print(f"  ✗ {tf.name}: FAIL（{type(e).__name__}: {e}）")
            total_f += 1
            continue
        status="✔" if f==0 else "✗"
        print(f"  {status} {tf.name}: {p} passed, {f} failed, {s} skipped")
        for fn,err in fails: print(f"      FAIL {fn}: {err}")
        total_p+=p; total_f+=f; total_s+=s
    print(f"\n总计: {total_p} passed, {total_f} failed, {total_s} skipped (+ 沙箱跳过的真机可跑)")
    sys.exit(1 if total_f else 0)

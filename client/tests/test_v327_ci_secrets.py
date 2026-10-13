"""V327 回归测试：CI 首跑失败（缺 Secrets）的三处修复。

背景（用户真实 Actions 日志）：checkout/Python/依赖/Docker 全通，最后 runner 报
`未设 HASHMM_OPENAI_BASE` 退出 1 —— 根因是 5 个 Secrets 没配，但：
  1. workflow 白装 28s 依赖 + 白下 41s 数据集才失败 → 加第一步"预检 Secrets"；
  2. runner 报错只说"见脚本头部用法"，CI 日志里无从下手 → 改成可执行的 Secrets 指引；
  3. 启动脚本模板的 token 占位串（"在此填…"）没换就重启的话鉴权可被猜 → ingest 拒绝占位串。
"""
import sys
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────── ① workflow 必须先预检 Secrets ───────────────────
def test_workflow_has_secrets_precheck_first():
    """预检步骤必须存在且排在 checkout 之前（缺模型 Secrets 1 秒内失败，不浪费分钟数）。

    V327 语义：模型三件套（OPENAI_BASE/MODEL 硬性，KEY 警告）缺了才失败；
    BACKEND_URL/TOKEN 改为【可选】——服务器在内网收不到回传时，自动切"仅 Artifact"模式。
    """
    wf = (_ROOT / ".github/workflows/docker-benchmarks.yml").read_text(encoding="utf-8")
    assert "预检 Secrets" in wf, "workflow 丢了 Secrets 预检步骤"
    assert wf.index("预检 Secrets") < wf.index("actions/checkout"), \
        "预检必须是第一步（在 checkout 之前），否则又要白等依赖安装"
    # 模型两个硬性 Secret 在失败分支；后端两个在警告分支（仅 Artifact 提示）
    assert "HASHMM_OPENAI_BASE" in wf and "HASHMM_OPENAI_MODEL" in wf
    assert "仅出 Artifact" in wf, "内网场景的仅-Artifact 提示丢了"
    # Artifact 上传步骤必须存在且 if always()（skip 也要存跳过原因）
    assert "actions/upload-artifact" in wf and "if: always()" in wf


def test_workflow_ci_uses_official_hf_endpoint():
    """CI 在海外，数据集下载应直连 huggingface.co（install.sh 默认镜像是给国内服务器的）。"""
    wf = (_ROOT / ".github/workflows/docker-benchmarks.yml").read_text(encoding="utf-8")
    assert "HF_ENDPOINT: https://huggingface.co" in wf


# ─────────────────── ② runner 报错要给 CI 可执行指引 ───────────────────
def test_runner_error_mentions_secrets_and_deepseek():
    src = (_ROOT / "scripts/remote_bench_runner.py").read_text(encoding="utf-8")
    assert "Secrets and variables" in src, "runner 缺 Secrets 配置指引"
    assert "api.deepseek.com" in src, "runner 应给出云端 API（DeepSeek）的具体填法示例"


# ─────────────────── ③ ingest 拒绝占位符/弱 token ───────────────────
def test_ingest_rejects_placeholder_and_short_token():
    """端点源码必须含占位串检测（'在此填'）与最短长度门槛（<16 拒绝）。"""
    src = (_ROOT / "hashmm/api/routes/selftest.py").read_text(encoding="utf-8")
    assert '"在此填" in tok' in src, "ingest 丢了占位符 token 防护"
    assert "len(tok) < 16" in src, "ingest 丢了弱 token 门槛"


def test_ingest_placeholder_logic_behaves():
    """用与端点相同的判定逻辑做行为验证（占位串/短串禁用；真随机串放行到鉴权阶段）。"""
    def endpoint_gate(tok: str) -> str:
        tok = (tok or "").strip()
        if not tok:
            return "disabled_empty"
        if "在此填" in tok or len(tok) < 16:
            return "disabled_placeholder"
        return "proceed_to_auth"

    assert endpoint_gate("") == "disabled_empty"
    assert endpoint_gate("在此填一个长随机串_如openssl_rand_hex_24生成") == "disabled_placeholder"
    assert endpoint_gate("abc123") == "disabled_placeholder"          # 太短
    assert endpoint_gate("f" * 48) == "proceed_to_auth"               # openssl rand -hex 24 长度


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  ✓ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

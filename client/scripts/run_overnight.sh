#!/usr/bin/env bash
# 通宵解析脚本 — 串行,每篇 60 分钟超时,可恢复
#
# 设计要点:
#   1. 串行解析(MinerU 已吃满 GPU,并行无收益且 OOM)
#   2. 每篇用临时目录 + 60 分钟 timeout(卡住的自动放弃)
#   3. 中断后再跑会自动跳过已解析的文档
#   4. nohup 后台运行,断开 ssh 不影响
#
# 用法:
#   nohup bash /root/autodl-tmp/run_overnight.sh > /root/autodl-tmp/overnight.log 2>&1 &
#   disown
#   # 关闭电脑没关系
#
# 早上查看:
#   tail -50 /root/autodl-tmp/overnight.log
#   ls /root/autodl-tmp/data/parsed/ | wc -l

set -u

# 12 篇精选 — 都是跨模态信息密度高的论文(figure / table 多)
PAPERS=(
  "1602.02255"  # DCMH (深度跨模态哈希经典)
  "1909.07217"  # DJSRH
  "2410.05983"  # LightRAG (有的话直接用,无需下)
  "2406.13858"  # M3DocRAG  (图表密集)
  "2411.02571"  # VisRAG
  "2412.13663"  # ColPali 扩展评测
  "2503.01776"  # ColQwen2
  "2004.12832"  # ColBERT 原论文
  "2502.18139"  # SigLIP 2
  "2103.00020"  # CLIP 原论文
  "2210.03629"  # ReAct
  "2308.08155"  # AutoGen
)

INPUT_DIR=/root/autodl-tmp/data/pdfs
OUTPUT_DIR=/root/autodl-tmp/data/parsed
TMP_DIR=/root/autodl-tmp/data/_tmp_parse
LOG_DIR=/root/autodl-tmp/logs/overnight
MAIN_LOG=/root/autodl-tmp/overnight.log

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$LOG_DIR" "$TMP_DIR"

cd /root/autodl-tmp
export HF_ENDPOINT=https://hf-mirror.com

log() { echo "$(date '+%F %T') $*" | tee -a "$MAIN_LOG"; }

log "=========================================="
log "通宵解析开始"
log "目标: ${#PAPERS[@]} 篇"
log "已有 parsed 文件: $(ls -1 "$OUTPUT_DIR"/*.json 2>/dev/null | wc -l)"
log "=========================================="

# ────── 第一阶段:批量下载 PDF ──────
log ""
log "[1/2] 下载 PDF..."

for arxiv_id in "${PAPERS[@]}"; do
    target="$INPUT_DIR/${arxiv_id}.pdf"
    if [ -f "$target" ] && [ "$(stat -c%s "$target" 2>/dev/null || echo 0)" -gt 50000 ]; then
        log "  [skip] $arxiv_id 已有"
        continue
    fi
    log "  [get ] $arxiv_id"
    curl -fsSL --connect-timeout 30 --max-time 600 \
        --retry 3 --retry-delay 5 \
        -H "User-Agent: Mozilla/5.0" \
        -o "$target" \
        "https://arxiv.org/pdf/${arxiv_id}.pdf" 2>>"$MAIN_LOG"
    if [ $? -eq 0 ] && [ "$(stat -c%s "$target" 2>/dev/null)" -gt 50000 ]; then
        log "    ok ($(stat -c%s "$target") bytes)"
    else
        log "    FAIL"
        rm -f "$target"  # 防止半下载文件被误判为存在
    fi
    sleep 2
done

# ────── 第二阶段:串行解析,每篇 60 分钟超时 ──────
log ""
log "[2/2] 解析 PDF..."

n_total=0
n_ok=0
n_fail=0
n_skip=0

for arxiv_id in "${PAPERS[@]}"; do
    n_total=$((n_total + 1))
    pdf="$INPUT_DIR/${arxiv_id}.pdf"
    
    if [ ! -f "$pdf" ]; then
        log "  [miss] $arxiv_id PDF 下载失败,跳过"
        n_fail=$((n_fail + 1))
        continue
    fi

    # 已解析则跳过(用 grep file_path 字段判断)
    if grep -l "${arxiv_id}.pdf" "$OUTPUT_DIR"/*.json 2>/dev/null | head -1 | grep -q .; then
        log "  [done] $arxiv_id 已解析,跳过"
        n_skip=$((n_skip + 1))
        continue
    fi

    log "  [parse $((n_total))/${#PAPERS[@]}] $arxiv_id 开始"
    
    # 用单独的临时目录,这样 01 脚本只看到一个 PDF
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"
    cp "$pdf" "$TMP_DIR/"
    
    sub_log="$LOG_DIR/${arxiv_id}.log"
    
    # 60 分钟超时,killer 后再 30s
    timeout --kill-after=30 3600 python scripts/01_parse_documents.py \
        --input "$TMP_DIR" \
        --output "$OUTPUT_DIR" \
        >"$sub_log" 2>&1
    rc=$?

    case $rc in
        0)
            log "    ✓ $arxiv_id 完成"
            n_ok=$((n_ok + 1))
            ;;
        124|137)
            log "    ✗ $arxiv_id TIMEOUT 60min"
            n_fail=$((n_fail + 1))
            # 强杀残留进程
            pkill -9 -f mineru 2>/dev/null || true
            pkill -9 -f "01_parse" 2>/dev/null || true
            sleep 15
            ;;
        *)
            log "    ✗ $arxiv_id 失败 rc=$rc (见 $sub_log)"
            n_fail=$((n_fail + 1))
            ;;
    esac
done

# 清理临时目录
rm -rf "$TMP_DIR"

# ────── 总结 ──────
log ""
log "=========================================="
log "通宵解析结束"
log "  尝试 $n_total / 成功 $n_ok / 跳过 $n_skip / 失败 $n_fail"
log "  parsed 目录文件数: $(ls -1 "$OUTPUT_DIR"/*.json 2>/dev/null | wc -l)"
log "=========================================="

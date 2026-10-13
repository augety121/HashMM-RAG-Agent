#!/bin/bash
# HashMM-RAG Agent — Production startup script
# Usage: bash scripts/start.sh

set -e
cd "$(dirname "$0")/.."

echo "╔══════════════════════════════════════════╗"
echo "║   HashMM-RAG Agent v12 — Starting...     ║"
echo "╚══════════════════════════════════════════╝"

# 1. Check Python
echo "[1/6] Checking Python..."
python3 --version || { echo "❌ Python3 not found"; exit 1; }

# 2. Check dependencies
echo "[2/6] Checking dependencies..."
python3 -c "import fastapi; print(f'  FastAPI {fastapi.__version__}')" 2>/dev/null || { echo "❌ FastAPI not installed"; exit 1; }
python3 -c "import pptx; print('  python-pptx ✓')" 2>/dev/null || echo "  ⚠️ python-pptx not installed (PPT generation will fallback to MD)"
python3 -c "import docx; print('  python-docx ✓')" 2>/dev/null || echo "  ⚠️ python-docx not installed (DOCX generation will fallback to MD)"
python3 -c "import openpyxl; print('  openpyxl ✓')" 2>/dev/null || echo "  ⚠️ openpyxl not installed (Excel generation unavailable)"

# 3. Check database
echo "[3/6] Checking database..."
python3 -c "from hashmm.api.database import init_db, run_migrations; init_db(); run_migrations(); print('  SQLite ✓')"

# 4. Run classification tests
echo "[4/6] Running classification tests..."
python3 tests/test_classification.py 2>/dev/null && echo "  Tests ✓" || echo "  ⚠️ Some classification tests failed"

# 5. Build frontend (if needed)
echo "[5/6] Checking frontend..."
if [ ! -d "frontend-next/out" ]; then
    echo "  Building frontend..."
    cd frontend-next && npm run build 2>&1 | tail -3 && cd ..
else
    echo "  Frontend build exists ✓"
fi

# 6. Start server
echo "[6/6] Starting server..."
echo ""
echo "  URL: http://0.0.0.0:6006"
echo "  Press Ctrl+C to stop"
echo ""
python3 -m uvicorn hashmm.api.server:app \
    --host 0.0.0.0 \
    --port 6006 \
    --timeout-keep-alive 300 \
    --log-level info

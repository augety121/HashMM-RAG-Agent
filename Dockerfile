FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip git curl && \
    ln -sf /usr/bin/python3.11 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependencies ─────────────────────────────────────────────────────
COPY pyproject.toml requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -e ".[hash,agent,eval,mcp]" && \
    pip install uvicorn[standard] fastapi "paddleocr<3" gradio "mcp>=1.2"

# ── Application ──────────────────────────────────────────────────────
COPY hashmm/ hashmm/
COPY scripts/ scripts/
COPY frontend/ frontend/
COPY README.md LICENSE ./

# ── Checkpoints (mount or COPY) ─────────────────────────────────────
# In production, mount checkpoints as a volume:
#   -v /path/to/checkpoints:/app/checkpoints
#   -v /path/to/indexes:/app/indexes
RUN mkdir -p checkpoints indexes data

EXPOSE 8000 7860

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default: start API server
CMD ["uvicorn", "hashmm.api.server:app", "--host", "0.0.0.0", "--port", "8000"]

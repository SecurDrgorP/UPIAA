# ═══════════════════════════════════════════════════════════════════════════════
# M8M Backend — Multi-stage Dockerfile
# Stage 1: deps only (cacheable)
# Stage 2: development (with source, hot-reload)
# Stage 3: production (optimized, non-root)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: Dependencies ────────────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /app

# System dependencies (tesseract for OCR node)
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Development ─────────────────────────────────────────────────────
FROM deps AS development

WORKDIR /app
COPY . .
RUN mkdir -p /app/storage/models

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── Stage 3: Production ─────────────────────────────────────────────────────
FROM deps AS production

RUN groupadd -r m8m && useradd -r -g m8m -d /app -s /sbin/nologin m8m

WORKDIR /app
COPY . .

# Pre-bake the demo fallback model into the image
RUN mkdir -p /app/storage/models && \
    python scripts/generate_demo_model.py

# Drop to non-root
RUN chown -R m8m:m8m /app
USER m8m

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

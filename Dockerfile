##############################################
#           BUILDER STAGE (Python deps)
##############################################
FROM python:3.12-slim AS builder
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt


##############################################
#              FINAL RUNTIME IMAGE
##############################################
FROM python:3.12-slim

WORKDIR /app

# System deps: llama.cpp (libgomp1) + curl + Tesseract OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Copy Python wheels from builder
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache /wheels/*

# Copy entire project
COPY . .

# llama.cpp environment (ajusta si quieres)
ENV LLAMA_THREADS=4
ENV LLAMA_BATCH=512

# Expose app port (Render usará $PORT, pero 8000 localmente)
EXPOSE 8000

# Healthcheck (usa curl que ya instalamos arriba)
HEALTHCHECK --interval=20s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default CMD (Render lo puede overridear)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

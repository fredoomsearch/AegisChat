##############################################
#           BUILDER STAGE (Python deps)
##############################################
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN set -eux; \
    pip install --upgrade pip; \
    pip wheel --wheel-dir /build/wheels -r requirements.txt; \
    ls -la /build/wheels

##############################################
#              FINAL RUNTIME IMAGE
##############################################
FROM python:3.12-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLAMA_THREADS=4 \
    LLAMA_BATCH=512

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
        tesseract-ocr \
        tesseract-ocr-spa \
        tesseract-ocr-eng; \
    rm -rf /var/lib/apt/lists/*

# Wheelhouse from builder
COPY --from=builder /build/wheels /wheels

# Copy requirements and install using wheels as cache
COPY requirements.txt .

RUN set -eux; \
    pip install --find-links=/wheels -r requirements.txt; \
    python -c "import sqlalchemy; print('SQLAlchemy OK:', sqlalchemy.__version__)"; \
    rm -rf /wheels

# App code
COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]


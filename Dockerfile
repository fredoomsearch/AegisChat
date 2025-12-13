##############################################
#           BUILDER STAGE (Python deps)
##############################################
FROM python:3.12-slim AS builder

WORKDIR /build

# Build tools for compiling any deps into wheels
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

# Runtime system deps
# - libgomp1: often needed by llama-cpp / some numeric libs
# - curl: for healthcheck
# - tesseract: OCR (spa + eng)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Install wheels built in builder stage
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# Copy app code
COPY . .

# Optional llama.cpp env
ENV LLAMA_THREADS=4
ENV LLAMA_BATCH=512

EXPOSE 8000

# Render supplies $PORT; use sh so env var expands
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]


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

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    nginx \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache /wheels/*

# Copy entire project
COPY . .

# llama.cpp environment
ENV LLAMA_THREADS=8
ENV LLAMA_BATCH=1024

# Expose app port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=20s --timeout=3s --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

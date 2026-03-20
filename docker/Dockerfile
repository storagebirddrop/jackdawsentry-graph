# Jackdaw Sentry Graph - Production Docker Image

FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.minimal.txt requirements.docker.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.docker.txt

FROM python:3.11-slim

RUN groupadd -r jackdaw && useradd -r -g jackdaw jackdaw

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

WORKDIR /app

COPY --chown=jackdaw:jackdaw . .

RUN mkdir -p /app/logs /app/data /app/uploads /app/exports && \
    chown -R jackdaw:jackdaw /app/logs /app/data /app/uploads /app/exports

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

USER jackdaw

CMD ["uvicorn", "src.api.graph_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]

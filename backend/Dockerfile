FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -e .

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5)"

CMD ["uvicorn", "innovation_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

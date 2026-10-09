FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

ENV PYTHONPATH=/app/src
ENV APP_ENV=prod
ENV TRADING_MODE=RESEARCH_ONLY
ENV ENABLE_LIVE_ORDERS=false

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from app.core.config import get_settings; get_settings()"

CMD ["python", "-m", "scripts.validate_environment"]

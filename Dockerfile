FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[crypto]" \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /data \
    && chown app:app /data

USER app
EXPOSE 8000
CMD ["uvicorn", "trading_platform.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM python:3.11.10-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libnss3 \
    libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY analytics-requirements.txt .
RUN pip install --no-cache-dir --requirement analytics-requirements.txt

COPY analytics_app /app/analytics_app
COPY wasp-app/vendor/plotly-2.35.2.min.js /app/analytics_app/static/vendor/plotly-2.35.2.min.js

RUN useradd --create-home --uid 10001 analytics && chown -R analytics:analytics /app
USER analytics
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "analytics_app.main:production_app", "--factory", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-access-log"]

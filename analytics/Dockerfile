FROM python:3.11.10-slim-bookworm

# Use the reachable HTTPS mirror on the Alibaba Cloud production host.
RUN sed -i -E 's|https?://deb.debian.org|https://mirrors.aliyun.com|g; s|https?://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i -E 's|https?://deb.debian.org|https://mirrors.aliyun.com|g; s|https?://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY analytics-requirements.txt .
RUN pip install --no-cache-dir --requirement analytics-requirements.txt

COPY analytics_app /app/analytics_app

RUN useradd --create-home --uid 10001 analytics && chown -R analytics:analytics /app
USER analytics
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "analytics_app.main:production_app", "--factory", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-access-log"]

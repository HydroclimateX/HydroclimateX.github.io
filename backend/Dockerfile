# WASP-Web FastAPI Backend
# Deployed on Alibaba Cloud ECS
FROM python:3.11-slim

LABEL maintainer="HydroclimateX Lab"
LABEL description="WASP-Web: WAvelet System Prediction API"

# Switch apt sources to Alibaba Cloud mirror (host-only replace, keeps /debian path)
# Handles both deb822 format (bookworm+) and legacy sources.list
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

# System dependencies for scientific Python (via Alibaba mirror)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (caching optimization)
# Uses Alibaba Cloud PyPI mirror for fast downloads in China
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 5 \
    -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 wasp && chown -R wasp:wasp /app
USER wasp

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Start
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

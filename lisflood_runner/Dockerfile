FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ gdal-bin libnetcdf-dev libnuma-dev make \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy==1.26.4 pillow==11.3.0

WORKDIR /app
COPY lisflood_runner /app/lisflood_runner

ENTRYPOINT ["python", "-m", "lisflood_runner.generate"]

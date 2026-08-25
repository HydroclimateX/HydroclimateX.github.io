FROM python:3.11-slim-bookworm

ARG LISFLOOD_URL=https://zenodo.org/record/4073011/files/LISFLOOD-FP-8.zip
ARG LISFLOOD_SHA256=a64fce20557217c628ff2ee2641275fcc576dc209326bb3d7cd6d7edad6f5808

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake curl g++ gdal-bin libnetcdf-dev libnuma-dev make unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fL --retry 3 "$LISFLOOD_URL" -o /tmp/lisflood.zip \
    && echo "$LISFLOOD_SHA256  /tmp/lisflood.zip" | sha256sum -c - \
    && unzip -q /tmp/lisflood.zip -d /tmp/source \
    && cmake -S /tmp/source/LISFLOOD-FP-trunk -B /tmp/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/build --parallel 2 \
    && install -D /tmp/build/lisflood /opt/lisflood/bin/lisflood \
    && /opt/lisflood/bin/lisflood -version | grep -q '8\.0\.3' \
    && cd /tmp/source/LISFLOOD-FP-trunk/testing/T025_Rain \
    && printf '\nacceleration\nhazard\n' >> rain_flat.par \
    && /opt/lisflood/bin/lisflood rain_flat.par >/tmp/lisflood-smoke.log \
    && test -s results_rain/res_rain.max \
    && test -s results_rain/res_rain.maxHaz \
    && test -s results_rain/res_rain.mass \
    && cd / \
    && rm -rf /tmp/build /tmp/source /tmp/lisflood.zip

RUN pip install --no-cache-dir numpy==1.26.4 pillow==11.3.0

WORKDIR /app
COPY lisflood_runner /app/lisflood_runner

ENTRYPOINT ["python", "-m", "lisflood_runner.generate"]

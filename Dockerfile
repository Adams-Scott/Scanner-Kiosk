# Scanner Kiosk: ScanSnap Scan button -> scan -> upload to Papra.
# Build and run with docker compose (see README).

FROM python:3.13-slim-trixie AS build
# python-sane is compiled against the SANE headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libsane-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.13-slim-trixie
# sane-utils: scanimage plus the SANE drivers (fujitsu). tzdata: local scan times.
RUN apt-get update \
 && apt-get install -y --no-install-recommends sane-utils tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels

WORKDIR /app
COPY *.py ./
COPY web ./web

ENV PYTHONUNBUFFERED=1 \
    KIOSK_DATA=/data \
    KIOSK_PORT=8080
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)"
CMD ["python", "kiosk.py"]

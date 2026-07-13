# syntax=docker/dockerfile:1.7
FROM python:3.11.9-slim-bookworm AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM python-base AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ make curl ca-certificates xz-utils perl \
    libqpdf-dev libmagic-dev libssl-dev zlib1g-dev libbz2-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade pip==24.2 build==1.2.2 && python -m pip install --prefix=/install .

FROM python-base AS web-runtime
RUN groupadd -g 10001 app && useradd -r -u 10001 -g app app \
    && apt-get update && apt-get install -y --no-install-recommends libqpdf29 libmagic1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install /usr/local
COPY src ./src
CMD ["uvicorn", "protected_file_analyzer.app:app", "--host", "0.0.0.0", "--port", "8088"]

FROM kalilinux/kali-rolling@sha256:8a1ea7281085ffef4963e82766c70869d7db910df88dcbb1f03d2899420b9577 AS worker-base
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ENV PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV HOME=/tmp/john-home
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential gcc g++ make curl ca-certificates \
    libqpdf-dev libqpdf30 libmagic1 libmagic-dev \
    john john-data p7zip-full yara oletools pdfid libimage-exiftool-perl file perl libcompress-raw-lzma-perl \
    && rm -rf /var/lib/apt/lists/*
RUN perl -MCompress::Raw::Lzma -e 'print "OK\\n"' \
    && test -x /usr/bin/7z2john \
    && printf '7z-probe\n' >/tmp/7z2john.txt \
    && 7z a -t7z -m0=Copy -pProbePass123! /tmp/7z2john.7z /tmp/7z2john.txt >/dev/null \
    && 7z2john /tmp/7z2john.7z >/tmp/7z2john.hash 2>/tmp/7z2john.err \
    && test -s /tmp/7z2john.hash \
    && grep -q '\$7z\$' /tmp/7z2john.hash \
    && ! grep -qi 'Compress::Raw::Lzma' /tmp/7z2john.err
RUN groupadd -g 10001 app && useradd -r -u 10001 -g app app

FROM worker-base AS worker-runtime
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip==26.1.2 build==1.2.2 \
    && pip install /app
RUN . /opt/venv/bin/activate \
    && python - <<'PY'
from protected_file_analyzer.runners import LocalContainerRunner
import json
caps = LocalContainerRunner().capabilities()
print(json.dumps(caps, ensure_ascii=False, indent=2))
required = [name for name in ("john", "zip2john", "7z2john", "pdf2john", "office2john") if not caps["extractors"].get(name)]
if required:
    raise SystemExit(f"Missing verified worker extractors: {required}")
PY
CMD ["sh", "-lc", "install -d -m 0700 \"$HOME\" \"$HOME/.john\" && exec /opt/venv/bin/python -m protected_file_analyzer"]

# Poliscope worker image.
#
# Same dependency set as the API image (see apps/api/Dockerfile) because both
# processes import the same `packages/` tree; kept as a separate Dockerfile
# only so each service gets its own CMD and can be scaled independently in
# docker-compose.yml.
#
#   docker build -f apps/worker/Dockerfile -t poliscope-worker .
#
FROM python:3.12-slim AS base
WORKDIR /app
RUN pip install --no-cache-dir --disable-pip-version-check uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY packages ./packages
COPY apps ./apps
COPY poliscope ./poliscope
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
# Tesseract + language packs for the OCR fallback in packages/papers/parser.py:
# scanned/image PDFs have no text layer, and PyMuPDF's OCR bridge needs the
# system libtesseract and traineddata at runtime. No new Python dependency.
#
# The deployment host is a mainland-China server where deb.debian.org is
# unreachably slow; swap the apt source to Alibaba's mirror first (same
# discipline as the ECR/DaoCloud image mirrors in docker-compose.yml) so the
# first build does not stall for tens of minutes on the language pack.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash poliscope
COPY --from=base --chown=poliscope:poliscope /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER poliscope
# Same pre-created, poliscope-owned object store root as the API image, so the
# shared poliscope-objects volume keeps the app user's ownership on first mount.
RUN mkdir -p /tmp/poliscope-objects
CMD ["python", "-m", "apps.worker.main"]

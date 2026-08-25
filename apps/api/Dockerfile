# Poliscope API image.
#
# Built from the repository root so it can copy `packages/`, `apps/`,
# `alembic.ini` and `migrations/` -- the API and the migrator share this same
# image (the migrator just overrides CMD), which keeps the two from drifting
# out of sync with each other's dependency set.
#
#   docker build -f apps/api/Dockerfile -t poliscope-api .
#
FROM python:3.12-slim AS base
WORKDIR /app
RUN pip install --no-cache-dir --disable-pip-version-check uv

# Dependencies first, so an unrelated code change does not invalidate this
# layer -- `uv sync` here only needs the lock file, not the source tree.
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
# Pre-create the private object store root as the app user. When the
# poliscope-objects named volume is first mounted here (docker-compose.yml),
# Docker copies this directory's ownership into the empty volume -- without
# this, the volume root is root:root 755 and the non-root poliscope user
# cannot create task/knowledge subdirectories, which broke uploads with a 500
# (round-10 report). RUN as poliscope so the directory is owned by poliscope.
RUN mkdir -p /tmp/poliscope-objects
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

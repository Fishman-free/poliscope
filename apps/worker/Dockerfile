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
RUN useradd --create-home --shell /bin/bash poliscope
COPY --from=base --chown=poliscope:poliscope /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER poliscope
# Same pre-created, poliscope-owned object store root as the API image, so the
# shared poliscope-objects volume keeps the app user's ownership on first mount.
RUN mkdir -p /tmp/poliscope-objects
CMD ["python", "-m", "apps.worker.main"]

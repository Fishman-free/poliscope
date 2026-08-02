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
RUN useradd --create-home --shell /bin/bash poliscope
COPY --from=base --chown=poliscope:poliscope /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER poliscope
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

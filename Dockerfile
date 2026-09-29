# Multi-stage: the build tools and the lockfile resolution do not ship.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY shelf ./shelf
RUN uv sync --frozen --no-dev


FROM python:3.12-slim
WORKDIR /app

# Non-root: this process is reachable from the open internet, and it has no
# reason to be able to write anything outside its own temp space.
RUN useradd --create-home --uid 10001 shelf
COPY --from=build --chown=shelf:shelf /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER shelf

EXPOSE 8000
# No migrations here, ever. The assistant's repository owns the schema.
CMD ["sh", "-c", "exec uvicorn shelf.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --proxy-headers --forwarded-allow-ips='*'"]

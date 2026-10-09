FROM oven/bun:1 AS web-build
WORKDIR /app/src/web
COPY src/web/package.json src/web/bun.lock ./
RUN bun install --frozen-lockfile
COPY src/web/ ./
RUN bun run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS python-build
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY tools/ ./tools/
RUN uv build --out-dir /dist

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    TELESCREEN_WEB_DIST=/app/web
WORKDIR /app
COPY --from=python-build /dist/ /dist/
RUN pip install --no-cache-dir /dist/*.whl \
    && rm -rf /dist \
    && useradd --create-home --uid 10001 telescreen \
    && mkdir -p /app/var/evidence \
    && chown -R telescreen:telescreen /app/var
COPY --from=web-build /app/src/web/dist/ /app/web/
COPY examples/police/ /app/examples/police/
USER telescreen
EXPOSE 8000

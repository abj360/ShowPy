FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap=0.12.0-1~deb13u1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-install-project

COPY stage ./stage
COPY tests ./tests
COPY schemas ./schemas
RUN uv sync --locked

CMD ["uv", "run", "pytest"]

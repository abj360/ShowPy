FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

COPY --from=uv /uv /usr/local/bin/uv

# No .git inside the build context, so hatch-vcs needs the version stated.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+docker
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY showpy ./showpy
RUN uv build --wheel --out-dir /dist

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS runtime

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1 \
    NOX_UV_NO_SYNC=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
RUN uv sync --locked --no-install-project --group dev

COPY --from=builder /dist /dist
RUN uv pip install --python /opt/venv/bin/python --no-deps /dist/*.whl

COPY noxfile.py ./
COPY tests ./tests
RUN chown -R 10001:10001 /app
USER 10001

CMD ["uv", "run", "--no-sync", "nox", "-s", "tests", "--", "tests", "--pyargs", "showpy"]

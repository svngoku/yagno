# ─────────────────────────────────────────────────────────────────────
#  Yagno — Docker image
#
#  Build:
#    docker build -t yagno .
#    docker build -t yagno --build-arg INSTALL_EXTRAS=all .
#
#  Run:
#    docker run --env-file .env yagno run specs/simple_researcher.yaml -i '"Hello"'
#    docker run --env-file .env -p 8000:8000 yagno serve specs/my_workflow.yaml
# ─────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Install uv for fast, lockfile-based dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install extras — default "all" installs every optional dependency.
# Override with: --build-arg INSTALL_EXTRAS=postgres,tavily
ARG INSTALL_EXTRAS=all

# Copy dependency manifests first (layer cache optimisation)
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual env at /app/.venv
RUN uv sync --frozen --no-dev --extra "${INSTALL_EXTRAS}" \
    && uv pip install --no-deps -e .

# Copy the rest of the application code
COPY . .

# Re-install the project itself (editable) so the CLI entrypoint works
RUN uv pip install --no-deps -e .


# ── Stage 2: Runtime ────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# System deps needed at runtime by psycopg and other native wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd --gid 1000 yagno \
    && useradd --uid 1000 --gid yagno --create-home yagno

WORKDIR /app

# Copy the fully-built virtual environment and application code from builder
COPY --from=builder /app /app

# Make sure the venv's bin is on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy and set entrypoint
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root
USER yagno

# FastAPI default port (used when running `yagno serve` or background mode)
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["--help"]

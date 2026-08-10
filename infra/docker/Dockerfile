# =============================================================================
# Ad Seller System — Multi-stage Dockerfile
# =============================================================================
# Stage 1: Build dependencies (cached layer)
# Stage 2: Production runtime (minimal image)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for some Python packages.
# git is required: pyproject declares a git dependency (iab-agentic-primitives)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git \
    && rm -rf /var/lib/apt/lists/*

# Install everything into a virtual env so we can copy it cleanly.
# No separate dependency layer: an editable hatchling build needs the
# source tree present, so a pyproject-only install cannot succeed and
# would only mask failures.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[redis]"

# ---------------------------------------------------------------------------
# Stage 2 — Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# libpq is needed at runtime for psycopg2/asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 seller && \
    useradd --uid 1000 --gid seller --create-home seller

# Copy virtual env from builder (all deps already resolved, no re-clone needed)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
WORKDIR /app
COPY --from=builder /build/src ./src
COPY --from=builder /build/pyproject.toml ./
COPY --from=builder /build/README.md ./

# Re-point the editable install at /app; dependencies already live in the
# copied venv, so skip resolution (which would also need git at runtime)
RUN pip install --no-cache-dir --no-deps -e .

# Switch to non-root user
USER seller

# Default environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_TYPE=postgres \
    DATABASE_URL=postgresql+asyncpg://seller:seller@postgres:5432/ad_seller \
    REDIS_URL=redis://redis:6379/0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command — uvicorn with production settings
CMD ["uvicorn", "ad_seller.interfaces.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--loop", "uvloop", \
     "--access-log"]
# Note: uvloop is optional; remove --loop uvloop if not installed


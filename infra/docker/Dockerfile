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

# Install build tools needed for some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency metadata first (cache-friendly)
COPY pyproject.toml ./

# Install dependencies into a virtual env so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install production deps + postgres + redis extras
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[redis]" \
    || pip install --no-cache-dir -e ".[redis]" 2>/dev/null; true

# Now copy source and install the package itself
COPY . .
RUN pip install --no-cache-dir -e ".[redis]"

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

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
WORKDIR /app
COPY --from=builder /build/src ./src
COPY --from=builder /build/pyproject.toml ./
COPY --from=builder /build/README.md ./

# Install the package in the runtime venv (editable so src/ is on path)
RUN pip install --no-cache-dir -e ".[redis]"

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


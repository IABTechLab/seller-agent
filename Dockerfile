FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed for any Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and README.md first (required by pyproject.toml metadata)
COPY pyproject.toml README.md ./

# Copy source code (needed for editable install)
COPY src/ ./src/

# Install the package with production dependencies
# Include postgres and redis for production use
RUN pip install --no-cache-dir -e ".[postgres,redis,gam]"

# Cloud Run injects PORT automatically; default to 8080 as fallback
ENV PORT=8080

EXPOSE 8080

# Run as non-root for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Start the FastAPI application with uvicorn
# Use shell form to read $PORT environment variable
CMD uvicorn ad_seller.interfaces.api.main:app --host 0.0.0.0 --port $PORT

FROM python:3.12.8-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# uv gives us --override support to pin lancedb below crewai's stated minimum
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src/ ./src/

# crewai>=1.10 declares lancedb>=0.29.2 but wheels above 0.25.3 are not
# published on PyPI for any platform as of this writing. The 0.25.3 API
# is compatible with all crewai features used here.
RUN echo "lancedb==0.25.3" > /tmp/uv-overrides.txt

RUN uv pip install --system --no-cache -e ".[postgres,redis,gam]" \
    --override /tmp/uv-overrides.txt

ENV PORT=8080
EXPOSE 8080

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD uvicorn ad_seller.interfaces.api.main:app --host 0.0.0.0 --port $PORT

# Stage 1: Build web assets
FROM node:22-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY web/ .
RUN npm run build

# Stage 2: Python application
FROM python:3.13-slim

# System deps for weasyprint (PDF generation)
# and eccodes (GRIB2 decoding via cfgrib)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libeccodes-dev \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (UID 2000 to match infra convention)
RUN groupadd -g 2000 app && useradd -u 2000 -g app -m app

WORKDIR /app

# Install app dependencies (copy pyproject first for layer caching)
COPY pyproject.toml .
RUN mkdir -p src/weatherbrief && \
    touch src/weatherbrief/__init__.py && \
    pip install --no-cache-dir -e . && \
    rm -rf src/weatherbrief

# Copy application source
COPY src/ src/
COPY configs/ configs/
COPY alembic/ alembic/
COPY alembic.ini .

# Copy web UI (source + built JS from Node stage)
COPY web/ web/
COPY --from=web-build /web/dist/ web/dist/

# Create data directory
RUN mkdir -p /app/data && chown app:app /app/data

USER app

ENV ENVIRONMENT=production
ENV DATA_DIR=/app/data

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health')"

# --proxy-headers: trust Caddy's X-Forwarded-Proto/X-Real-IP so request.base_url
# is https and client IPs are real. Trusting all peers is safe because compose
# binds the port to the host loopback only (127.0.0.1:8020) — nothing but the
# local reverse proxy can reach uvicorn.
CMD ["uvicorn", "weatherbrief.api.app:app", "--host", "0.0.0.0", "--port", "8020", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

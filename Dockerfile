FROM python:3.13-slim

# System deps for weasyprint (PDF generation) and git (euro-aip install)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (UID 2000 to match infra convention)
RUN groupadd -g 2000 app && useradd -u 2000 -g app -m app

WORKDIR /app

# Install euro-aip from GitHub (replaces local path in pyproject.toml)
RUN pip install --no-cache-dir \
    "euro-aip @ git+https://github.com/roznet/rzflight.git#subdirectory=euro_aip"

# Install app dependencies (copy pyproject first for layer caching)
COPY pyproject.toml .
# Strip the local euro-aip path (already installed from GitHub above),
# create minimal package structure, then install remaining deps
RUN mkdir -p src/weatherbrief && \
    touch src/weatherbrief/__init__.py && \
    sed -i '/euro-aip @/d' pyproject.toml && \
    pip install --no-cache-dir -e . && \
    rm -rf src/weatherbrief

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Copy web UI
COPY web/ web/

# Create data directory
RUN mkdir -p /app/data && chown app:app /app/data

USER app

ENV ENVIRONMENT=production
ENV DATA_DIR=/app/data

EXPOSE 8020

CMD ["uvicorn", "weatherbrief.api.app:app", "--host", "0.0.0.0", "--port", "8020"]

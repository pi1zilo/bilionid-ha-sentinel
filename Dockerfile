FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Create application user and data directory
RUN useradd -u 1000 -m -s /bin/bash appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app

# Copy application source code
COPY --chown=appuser:appuser app /app/app
COPY --chown=appuser:appuser config /app/config
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 29010

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, sys, urllib.request; port = os.environ.get('HA_PORT', '29010'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health').getcode() == 200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]

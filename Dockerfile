ARG PYTHON_IMAGE=python:3.11-slim@sha256:65bd3d9f1d7fa268dd1909973ba7eb4bdf41f6dcf2cfbe76ba9528b170f3fd79
FROM ${PYTHON_IMAGE}

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG INSTALL_JOURNALCTL=false
RUN if [ "$INSTALL_JOURNALCTL" = true ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends systemd && \
        rm -rf /var/lib/apt/lists/*; \
    fi

# Install Python requirements
COPY requirements.txt constraints.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Create application user and data directory
RUN useradd -u 1000 -m -s /bin/bash appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app

# Copy application source code
COPY --chown=appuser:appuser app /app/app
COPY --chown=appuser:appuser config /app/config

USER appuser

EXPOSE 29010

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-m", "app.core.healthcheck"]

CMD ["python", "-m", "app.main"]

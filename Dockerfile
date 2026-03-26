# =============================================================================
# Dockerfile — Smart Cooler Hybrid Pipeline (local side)
# Handles frame extraction and RunPod SSH communication.
# No GPU required on the local machine.
# =============================================================================

FROM python:3.11-slim

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user matching local user (UID/GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m appuser

# Set working directory
WORKDIR /app

# Install Python dependencies as root first
COPY requirements_local.txt .
RUN pip install --no-cache-dir -r requirements_local.txt

# Copy application files
COPY pipeline_app.py .
COPY app_config.py .

# Create mount point directories and give ownership to appuser
RUN mkdir -p /videos /output /ssh && \
    chown -R appuser:appuser /app /videos /output /ssh

# Switch to non-root user
USER appuser

# Default command
ENTRYPOINT ["python3", "pipeline_app.py"]

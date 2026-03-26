FROM python:3.11-slim


RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /app

COPY requirements_local.txt .
RUN pip install --no-cache-dir -r requirements_local.txt


COPY pipeline_app.py .
COPY app_config.py .


RUN mkdir -p /videos /output /ssh

ENTRYPOINT ["python3", "pipeline_app.py"]

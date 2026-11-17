FROM python:3.12-slim
# ffmpeg with libass (caption burn-in) + a bold font that the .ass style names.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data HF_HOME=/data/models

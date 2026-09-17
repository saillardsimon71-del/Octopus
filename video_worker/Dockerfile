FROM node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=production \
    PUPPETEER_SKIP_DOWNLOAD=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 \
       python3-pip \
       ffmpeg \
       chromium \
       ca-certificates \
       fonts-dejavu \
       fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY video_worker/requirements.txt /app/video_worker/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/video_worker/requirements.txt

COPY remotion/package*.json /app/remotion/
RUN cd /app/remotion && npm ci

COPY . /app

CMD ["python3", "-m", "video_worker.runpod_handler"]

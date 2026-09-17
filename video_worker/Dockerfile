FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Image de fondation uniquement. Le pipeline FORGE doit ajouter Node/Remotion,
# Chromium, FFmpeg et le backend TTS après validation des versions exactes.
COPY . /app

CMD ["python", "-m", "video_worker.worker"]

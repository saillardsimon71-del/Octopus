FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m pip install --no-cache-dir pytest==9.0.3 customtkinter==5.2.2 "Pillow>=10,<13"

WORKDIR /workspace

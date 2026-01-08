# Stage 1: Build Svelte frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python app
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for pyatv and FFmpeg for transcoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app ./app

# Copy Svelte build output to static directory
COPY --from=frontend-builder /app/frontend/dist ./app/static

# Build metadata (set via --build-arg or defaults to "dev")
ARG BUILD_TIME=unknown
ARG GIT_COMMIT=unknown
ENV BUILD_TIME=$BUILD_TIME
ENV GIT_COMMIT=$GIT_COMMIT

ENV PORT=8754
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT

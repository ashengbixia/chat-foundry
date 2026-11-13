FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/cache/huggingface
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ /app/backend/
RUN useradd --uid 10001 --create-home app && mkdir -p /data /cache/huggingface && chown -R app:app /data /cache
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "100", "--backlog", "256", "--timeout-keep-alive", "5", "--proxy-headers", "--forwarded-allow-ips", "172.30.204.2"]

FROM caddy:2-alpine AS gateway
COPY --from=frontend /build/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile

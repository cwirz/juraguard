FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && addgroup --system gateway \
    && adduser --system --ingroup gateway gateway \
    && mkdir -p /data \
    && chown gateway:gateway /data

COPY --chown=gateway:gateway . .
RUN chmod +x /app/docker/entrypoint.sh
USER gateway
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready/', timeout=2)"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]

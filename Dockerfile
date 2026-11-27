FROM python:3.12-slim

WORKDIR /app

COPY server.py cli.py ./
COPY xbh ./xbh
COPY web ./web

RUN mkdir -p /app/data/media /app/exports

ENV PYTHONUNBUFFERED=1 \
    XBH_HOST=0.0.0.0 \
    XBH_PORT=7799 \
    XBH_DB_PATH=/app/data/bookmarks.db \
    XBH_EXPORTS_DIR=/app/exports

EXPOSE 7799
VOLUME ["/app/data", "/app/exports"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7799/api/health', timeout=3)" || exit 1

CMD ["python", "server.py"]

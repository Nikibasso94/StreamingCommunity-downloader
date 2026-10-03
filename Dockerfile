FROM python:3.11-slim

# tzdata: resolves TZ (e.g. Europe/Rome) for log timestamps.
# util-linux: setpriv, used by docker-entrypoint.sh to drop from root to
# PUID/PGID when one is set.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The one path the app writes to that is not expected to already be a
# mounted volume with its own ownership (VIDEOS_DIR, /app/config) — writable
# by whichever PUID the entrypoint switches to at runtime, same as /tmp.
RUN mkdir -p tmp && chmod 1777 tmp && chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "main.py"]

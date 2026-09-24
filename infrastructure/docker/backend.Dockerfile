FROM python:3.11.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home visionops
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock
COPY . /app
RUN mkdir -p /data/artifacts /data/evidence /data/trusted_keys /data/mlflow && chown -R visionops:visionops /data
USER 10001
# One process cannot serve a 10 000-device heartbeat round: the single uvicorn worker
# saturates one core (GIL) at roughly 25 writes/s, which is below the 10 000/60 s
# liveness rate the fleet controller is asked to sustain. Four workers use the host's
# cores. Trade-off, documented in docs/CURRENT_VERIFIED_STATE.md: the in-process
# WebSocket fan-out is per worker, so a browser connected to worker A does not see an
# event raised by worker B; REST polling remains the source of truth.
CMD ["uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000","--workers","4"]

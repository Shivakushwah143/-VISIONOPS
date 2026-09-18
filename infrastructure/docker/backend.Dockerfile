FROM python:3.11.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home visionops
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock
COPY . /app
RUN mkdir -p /data/artifacts /data/evidence /data/trusted_keys /data/mlflow && chown -R visionops:visionops /data
USER 10001
CMD ["uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000"]

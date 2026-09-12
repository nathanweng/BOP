FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MEDIA_ROOT=/data/media
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY bop_api ./bop_api
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /data/media
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn bop_api.main:app --host 0.0.0.0 --port 8000"]


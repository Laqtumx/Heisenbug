# CleanSlate API. Build from the repository root:
#   docker build -f backend/Dockerfile -t cleanslate-api .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg[binary] ships wheels, but reportlab needs libfreetype at runtime for
# some fonts. Keep the image slim and install only what is missing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfreetype6 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/db ./db

# Uploads land here. Mount a volume to persist them across restarts.
RUN mkdir -p /app/var/uploads

EXPOSE 8000

# Render, Railway and Fly set PORT. Default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

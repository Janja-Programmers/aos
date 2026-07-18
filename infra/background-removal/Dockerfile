FROM python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# rembg stores downloaded model files here. In production we can mount this
# directory if we want model cache persistence across container rebuilds.
ENV U2NET_HOME=/models/rembg

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgomp1 \
        libglib2.0-0 \
    && mkdir -p /models/rembg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /app/requirements.lock

RUN python -m pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && python -m pip check

COPY app /app/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

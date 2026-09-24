FROM python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgomp1 \
        patchelf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /app/requirements.lock

RUN python -m pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && python -m pip check \
    && python - <<'PY'
import glob
import os
import subprocess
import ctranslate2

package_dir = os.path.dirname(ctranslate2.__file__)
shared_objects = glob.glob(os.path.join(package_dir, "*.so"))

for so_path in shared_objects:
    print(f"Clearing executable stack flag: {so_path}")
    subprocess.run(
        ["patchelf", "--clear-execstack", so_path],
        check=False,
    )
PY

COPY app /app/app

RUN addgroup --system --gid 10001 aos \
    && adduser --system --uid 10001 --ingroup aos --home /nonexistent --no-create-home aos \
    && chown -R aos:aos /app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

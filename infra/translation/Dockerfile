FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgomp1 \
        patchelf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt \
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

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]